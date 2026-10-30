import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from agents_cli.bus import get_or_create_conversation, remember_session
from agents_cli.cli import main
from agents_cli.home import cli_command, prepend_bin_dirs
from agents_cli.install import _norm, skill_destinations, skill_source, unix_wrapper_body, wrapper_body


def run_main(argv):
    buf = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        code = main(argv)
    return code, buf.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTS_HOME"] = self.tmp.name
        self.cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self.cwd)
        os.environ.pop("AGENTS_HOME", None)
        os.environ.pop("AGENTS_CONVERSATION", None)
        os.environ.pop("AGENTS_SELF", None)
        self.tmp.cleanup()

    def test_whoami_unknown(self):
        os.environ.pop("AGENTS_SELF", None)
        code, out, _ = run_main(["whoami"])
        self.assertEqual(code, 0)
        self.assertTrue(out.strip())

    def test_delegate_dry_run(self):
        os.environ["AGENTS_SELF"] = "grok"
        code, out, _ = run_main(
            [
                "delegate",
                "claude",
                "sonnet",
                "high",
                "--dry-run",
                "--json",
                "--prompt",
                "review this",
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("conversation", out)
        self.assertIn("--dangerously-skip-permissions", out)

    def test_send_and_inbox(self):
        os.environ["AGENTS_SELF"] = "grok"
        code, out, _ = run_main(["send", "claude", "--prompt", "ping"])
        self.assertEqual(code, 0)
        cid = out.strip()
        code, inbox, _ = run_main(["inbox", "--agent", "claude"])
        self.assertEqual(code, 0)
        self.assertIn("ping", inbox)
        self.assertIn(cid, inbox)

    def test_missing_prompt(self):
        code, _, err = run_main(["delegate", "grok"])
        self.assertEqual(code, 2)
        self.assertIn("prompt", err)

    def test_followup_reuses_session_model_and_effort(self):
        os.environ["AGENTS_SELF"] = "grok"
        meta = get_or_create_conversation("cdeadbeef000", os.getcwd(), "grok")
        remember_session(meta, "claude", "sess-abc", model="sonnet", effort="xhigh")
        code, out, err = run_main(
            [
                "delegate",
                "claude",
                "--conversation",
                "cdeadbeef000",
                "--dry-run",
                "--json",
                "--prompt",
                "now write tests",
            ]
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["conversation"], "cdeadbeef000")
        self.assertEqual(data["resume"], "sess-abc")
        self.assertTrue(data["resumed"])
        self.assertEqual(data["model"], "sonnet")
        self.assertEqual(data["effort"], "xhigh")
        self.assertIn("--resume", data["argv"])
        self.assertIn("sess-abc", data["argv"])
        self.assertIn("conversation=cdeadbeef000", err)
        self.assertIn("resumed=yes", err)

    def test_new_ignores_inherited_conversation(self):
        os.environ["AGENTS_SELF"] = "grok"
        os.environ["AGENTS_CONVERSATION"] = "cdeadbeef000"
        get_or_create_conversation("cdeadbeef000", os.getcwd(), "grok")
        code, out, _ = run_main(
            [
                "delegate",
                "claude",
                "--new",
                "--dry-run",
                "--json",
                "--prompt",
                "unrelated task",
            ]
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertNotEqual(data["conversation"], "cdeadbeef000")
        self.assertFalse(data["resumed"])
        self.assertIsNone(data["resume"])

    def test_skill_source_exists(self):
        self.assertTrue((skill_source() / "SKILL.md").is_file())

    def test_destinations_cover_all_harnesses(self):
        text = " ".join(str(path) for path in skill_destinations())
        for name in (".claude", ".codex", ".gemini", ".grok", "skills"):
            self.assertIn(name, text)

    def test_wrapper_points_at_repo(self):
        body = wrapper_body()
        self.assertIn("agents.py", body)
        self.assertIn("PYTHONPATH", body)
        unix = unix_wrapper_body()
        self.assertTrue(unix.startswith("#!/bin/sh\n"))
        self.assertIn("agents.py", unix)
        self.assertIn('exec python -u', unix)

    def test_cli_command_is_absolute_python(self):
        cmd = cli_command()
        self.assertIn("python -u", cmd)
        self.assertIn("agents.py", cmd)

    def test_prepend_bin_dirs_puts_local_bin_first(self):
        env = {"PATH": "C:\\Windows\\system32"}
        prepend_bin_dirs(env)
        first = env["PATH"].split(os.pathsep)[0]
        self.assertTrue(first.lower().endswith(os.path.join(".local", "bin").lower()) or ".local" in first.lower())

    def test_norm_strips_extended_path(self):
        left = Path(r"\\?\C:\Users\augus\Desktop\agents_cli\skill\delegate")
        right = Path(r"C:\Users\augus\Desktop\agents_cli\skill\delegate")
        self.assertEqual(_norm(left), _norm(right))
