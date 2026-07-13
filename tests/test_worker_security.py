from __future__ import annotations

import subprocess
import unittest
from unittest.mock import MagicMock, patch

import app.runner as runner


class WorkerSecurityTests(unittest.TestCase):
    def test_timeout_is_clamped_to_supported_range(self):
        self.assertEqual(runner.worker_timeout_seconds("1"), 5.0)
        self.assertEqual(runner.worker_timeout_seconds("60"), 60.0)
        self.assertEqual(runner.worker_timeout_seconds("999"), 120.0)
        self.assertEqual(runner.worker_timeout_seconds("invalid"), 35.0)

    def test_worker_environment_excludes_provider_secrets(self):
        with patch.dict(
            runner.os.environ,
            {"OPEN_ROUTER_KEY": "secret", "DEEP_SEEK_KEY": "secret", "PATH": "/usr/bin"},
            clear=True,
        ):
            env = runner._worker_environment()

        self.assertNotIn("OPEN_ROUTER_KEY", env)
        self.assertNotIn("DEEP_SEEK_KEY", env)
        self.assertEqual(env["PYTHONNOUSERSITE"], "1")

    def test_timeout_kills_worker_process_group(self):
        process = MagicMock(pid=1234)
        process.communicate.side_effect = [subprocess.TimeoutExpired(["worker"], 5), ("", "")]
        with patch.object(runner.subprocess, "Popen", return_value=process), patch.object(
            runner.os, "killpg"
        ) as killpg, patch.object(runner, "WORKER_TIMEOUT_SECONDS", 5):
            with self.assertRaises(subprocess.TimeoutExpired):
                runner._run_local(runner.ROOT)

        killpg.assert_called_once_with(1234, runner.signal.SIGKILL)


if __name__ == "__main__":
    unittest.main()
