"""Offline reporting tests: keep recovery requirements unchanged."""
import unittest
from unittest.mock import patch
import test_traffic_capacity as fixtures
from opspilot_integration.traffic import control as c, evidence as e


class DiagnosticsTests(unittest.TestCase):
    def check(self, **kwargs):
        return fixtures.RecoveryTests().check(**kwargs)

    def test_failed_latency_records_measurements_and_exact_check(self):
        result = self.check(metrics={"p95": .9, "rps": 6, "error_ratio": 0})
        self.assertFalse(result["recovered"])
        d = result["diagnostics"]
        self.assertEqual(d["last_observation"]["after"]["p95"], .9)
        self.assertIn("server_p95_within_400ms", d["last_observation"]["failed_checks"])
        self.assertEqual(d["thresholds"]["p95_seconds_max"], .4)

    def test_throughput_failure_named(self):
        result = self.check(metrics={"p95": .2, "rps": 2, "error_ratio": 0})
        self.assertIn("server_throughput_retained_80pct", result["diagnostics"]["last_observation"]["failed_checks"])

    def test_error_failure_named(self):
        result = self.check(metrics={"p95": .2, "rps": 6, "error_ratio": .02})
        self.assertIn("server_errors_below_1pct", result["diagnostics"]["last_observation"]["failed_checks"])

    def test_stopped_load_preserves_validation_reason(self):
        result = self.check(change=lambda v: v["load"].update(burst=False))
        d = result["diagnostics"]
        self.assertEqual(d["measurement_samples"], 0)
        self.assertGreater(d["validation_failures"], 0)
        self.assertEqual(d["last_validation_error"]["reason"], "Workload/Service/load changed")

    def test_frozen_backend_never_recovers(self):
        result = self.check(frozen=True)
        self.assertFalse(result["recovered"])
        self.assertIn("both_pods_serving", result["diagnostics"]["last_observation"]["failed_checks"])

    def test_valid_recovery_still_passes(self):
        self.assertTrue(self.check()["recovered"])
