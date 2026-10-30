import os
import unittest
from unittest import mock

from agents_cli import detect


class DetectTests(unittest.TestCase):
    def _env(self, **extra):
        env = {k: v for k, v in os.environ.items() if k not in ("AGENTS_SELF", "CLAUDECODE", "GEMINI_CLI")}
        env.update(extra)
        return mock.patch.dict(os.environ, env, clear=True)

    def test_explicit_env_wins(self):
        with self._env(AGENTS_SELF="Antigravity", CLAUDECODE="1"):
            with mock.patch.object(detect.procs, "ancestor_names", return_value=["codex.exe"]):
                self.assertEqual(detect.whoami(), "gemini")

    def test_ancestor_match(self):
        with self._env():
            with mock.patch.object(detect.procs, "ancestor_names", return_value=["bash.exe", "node.exe", "codex.exe"]):
                self.assertEqual(detect.whoami(), "codex")

    def test_ancestor_skips_shells_and_picks_nearest(self):
        names = ["cmd.exe", "agy.exe", "claude.exe"]
        with self._env():
            with mock.patch.object(detect.procs, "ancestor_names", return_value=names):
                self.assertEqual(detect.whoami(), "gemini")

    def test_env_hint_claudecode(self):
        with self._env(CLAUDECODE="1"):
            with mock.patch.object(detect.procs, "ancestor_names", return_value=["bash.exe", "node.exe"]):
                self.assertEqual(detect.whoami(), "claude")

    def test_env_hint_gemini_cli(self):
        with self._env(GEMINI_CLI="1"):
            with mock.patch.object(detect.procs, "ancestor_names", return_value=[]):
                self.assertEqual(detect.whoami(), "gemini")

    def test_unknown(self):
        with self._env():
            with mock.patch.object(detect.procs, "ancestor_names", return_value=["python.exe", "pwsh.exe"]):
                self.assertEqual(detect.whoami(), "unknown")

    def test_ancestor_failure_is_unknown(self):
        with self._env():
            with mock.patch.object(detect.procs, "ancestor_names", side_effect=RuntimeError("x")):
                self.assertEqual(detect.whoami(), "unknown")

    def test_parent_process_name(self):
        with mock.patch.object(detect.procs, "ancestor_names", return_value=["bash.exe"]):
            self.assertEqual(detect.parent_process_name(), "bash.exe")
        with mock.patch.object(detect.procs, "ancestor_names", return_value=[]):
            self.assertIsNone(detect.parent_process_name())

    def test_whoami_is_fast(self):
        import time

        with self._env():
            started = time.monotonic()
            detect.whoami()
            self.assertLess(time.monotonic() - started, 1.0)


if __name__ == "__main__":
    unittest.main()
