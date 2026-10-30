import json
import tempfile
import unittest
from pathlib import Path

from agents_cli.harness import build_launch, extract_text, output_is_error


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.job = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _launch(self, agent, session_id=None, auto_approve=True):
        return build_launch(
            agent=agent,
            model="m",
            effort="high",
            prompt="hello",
            cwd=str(self.job),
            job_dir=self.job / agent,
            config={"binaries": {agent: agent}},
            from_agent="grok",
            conversation="c1",
            depth=1,
            session_id=session_id,
            auto_approve=auto_approve,
            allow_missing=True,
        )

    def test_grok_argv(self):
        launch = self._launch("grok")
        self.assertIn("--prompt-file", launch.argv)
        self.assertIn("--always-approve", launch.argv)
        self.assertIn("--reasoning-effort", launch.argv)
        self.assertIsNone(launch.stdin)

    def test_claude_argv(self):
        launch = self._launch("claude")
        self.assertIn("-p", launch.argv)
        self.assertIn("hello", launch.argv)
        self.assertIn("--dangerously-skip-permissions", launch.argv)
        self.assertIn("--effort", launch.argv)
        path = launch.env.get("PATH", "")
        self.assertTrue(path.lower().find(".local") >= 0 or path.lower().find(".agents") >= 0)

    def test_codex_argv_and_stdin(self):
        launch = self._launch("codex")
        self.assertEqual(launch.argv[1], "exec")
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", launch.argv)
        self.assertIn("--json", launch.argv)
        self.assertIn("--thread-source", launch.argv)
        self.assertEqual(launch.argv[-1], "-")
        self.assertEqual(launch.stdin, "hello")
        self.assertTrue(str(launch.last_message_file).endswith("last_message.txt"))
        self.assertEqual(launch.env.get("CODEX_INTERNAL_ORIGINATOR_OVERRIDE"), "codex-app-server")

    def test_codex_resume(self):
        launch = self._launch("codex", session_id="abc")
        self.assertIn("resume", launch.argv)
        self.assertIn("abc", launch.argv)

    def test_gemini_agy_vs_cli(self):
        agy = build_launch(
            agent="gemini",
            model="gemini-3.8-flash",
            effort="high",
            prompt="x",
            cwd=str(self.job),
            job_dir=self.job / "agy",
            config={"binaries": {"gemini": r"C:\agy\agy.exe"}},
            from_agent="claude",
            conversation="c1",
            depth=1,
            session_id=None,
            auto_approve=True,
            allow_missing=True,
        )
        self.assertIn("--effort", agy.argv)
        gem = build_launch(
            agent="gemini",
            model="gemini-3.8-flash",
            effort="high",
            prompt="x",
            cwd=str(self.job),
            job_dir=self.job / "gem",
            config={"binaries": {"gemini": r"C:\npm\gemini.cmd"}},
            from_agent="claude",
            conversation="c1",
            depth=1,
            session_id=None,
            auto_approve=True,
            allow_missing=True,
        )
        self.assertIn("-y", gem.argv)
        self.assertNotIn("--effort", gem.argv)

    def test_extract_grok_json(self):
        text, session = extract_text("grok", json.dumps({"text": "ok", "sessionId": "s1"}))
        self.assertEqual(text, "ok")
        self.assertEqual(session, "s1")

    def test_extract_claude_json(self):
        text, session = extract_text(
            "claude",
            json.dumps({"result": "done", "session_id": "sess"}),
        )
        self.assertEqual(text, "done")
        self.assertEqual(session, "sess")

    def test_claude_unrecognized_model_is_error(self):
        payload = json.dumps(
            {
                "is_error": True,
                "terminal_reason": "api_error",
                "api_error_status": 404,
                "result": "There's an issue with the selected model (haiku-5.5).",
                "session_id": "sess",
            }
        )
        self.assertTrue(output_is_error(payload))
        text, session = extract_text("claude", payload)
        self.assertIn("haiku-5.5", text)
        self.assertEqual(session, "sess")

    def test_extract_codex_last_message_file(self):
        path = self.job / "last.txt"
        path.write_text("final answer", encoding="utf-8")
        text, _ = extract_text("codex", "", last_message_file=path)
        self.assertEqual(text, "final answer")

    def test_extract_codex_session_from_stderr_banner(self):
        stderr = "OpenAI Codex v0.159.2\nsession id: 01a15c8b-3006-72b2-8a20-66cb201ca48c\n"
        text, session = extract_text("codex", "ok", stderr=stderr)
        self.assertEqual(text, "ok")
        self.assertEqual(session, "01a15c8b-3006-72b2-8a20-66cb201ca48c")

    def test_extract_codex_session_from_jsonl_payload(self):
        stdout = json.dumps(
            {
                "type": "session_meta",
                "payload": {"session_id": "01aaaaaaaaaaaaaaaaaaaaaaaa"},
            }
        )
        _, session = extract_text("codex", stdout)
        self.assertEqual(session, "01aaaaaaaaaaaaaaaaaaaaaaaa")
