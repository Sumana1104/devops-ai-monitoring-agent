"""Publishing cannot silently bypass live proofs, tests, scope or user opt-in."""
from types import SimpleNamespace
from contextlib import redirect_stdout
import io
import unittest
from unittest.mock import patch
from opspilot_integration import finalize_v1 as finish


class FinalizerTests(unittest.TestCase):
    def setUp(self):
        output = redirect_stdout(io.StringIO())
        output.__enter__(); self.addCleanup(output.__exit__, None, None, None)
        def git(*args, **kwargs):
            if args[:2] == ("branch", "--show-current"): return SimpleNamespace(stdout="main\n",returncode=0)
            if args[:3] == ("remote", "get-url", "origin"): return SimpleNamespace(stdout="https://github.com/Sumana1104/devops-ai-monitoring-agent.git\n",returncode=0)
            return SimpleNamespace(stdout="",returncode=0)
        patcher=patch.object(finish,"git",side_effect=git)
        self.git=patcher.start();self.addCleanup(patcher.stop)
        patcher=patch.object(finish,"source_files",return_value=["opspilot_integration/bridge.py"])
        patcher.start();self.addCleanup(patcher.stop)
        patcher=patch.object(finish.readiness_demo,"bridge_health")
        self.health=patcher.start();self.addCleanup(patcher.stop)
        patcher=patch.object(finish.scenario,"verify",return_value=True)
        self.verify=patcher.start();self.addCleanup(patcher.stop)
        patcher=patch.object(finish.subprocess,"run")
        self.run=patcher.start();self.addCleanup(patcher.stop)

    def test_publish_requires_explicit_confirmation(self):
        with self.assertRaises(ValueError):finish.finalize(True,False)
        self.git.assert_not_called();self.run.assert_not_called()

    def test_missing_live_proof_stops_before_tests_or_git_writes(self):
        self.verify.return_value=False
        with self.assertRaises(ValueError):finish.finalize(True,True)
        self.run.assert_not_called()
        self.assertFalse(any(c.args[0] in {"add","commit","push"} for c in self.git.call_args_list))

    def test_check_only_never_stages_commits_or_pushes(self):
        finish.finalize()
        self.assertEqual(self.run.call_count,2)
        self.assertFalse(any(c.args[0] in {"add","commit","push"} for c in self.git.call_args_list))

    def test_test_failure_prevents_git_upload(self):
        self.run.side_effect=RuntimeError("Offline test failed")
        with self.assertRaises(RuntimeError):finish.finalize(True,True)
        self.assertFalse(any(c.args[0] in {"add","commit","push"} for c in self.git.call_args_list))

    def test_publish_uses_only_reviewed_files_and_normal_main_push(self):
        finish.finalize(True,True)
        self.assertTrue(any(c.args == ("add","--","opspilot_integration/bridge.py") for c in self.git.call_args_list))
        self.assertTrue(any(c.args == ("push","origin","main") for c in self.git.call_args_list))
        self.assertFalse(any("--force" in c.args or "reset" in c.args for c in self.git.call_args_list))


if __name__ == "__main__":unittest.main()
