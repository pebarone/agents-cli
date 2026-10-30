import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from agents_cli.harness import (
    Launch,
    build_launch,
    extract_text,
    failure_text,
    output_is_error,
    resume_missing,
    run_launch,
)
from agents_cli.parse import ParseError


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
        self.assertNotIn("hello", launch.argv)
        self.assertEqual(launch.stdin, "hello")
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
        self.assertNotIn("-C", launch.argv)
        self.assertEqual(launch.cwd, str(self.job))
        self.assertEqual(launch.argv[-1], "-")

    def test_codex_never_passes_cwd_flag(self):
        for session in (None, "abc"):
            launch = self._launch("codex", session_id=session)
            self.assertNotIn("-C", launch.argv)
            self.assertEqual(launch.cwd, str(self.job))

    def _codex_with_binary(self, binary, model="gpt-6.1-sol", effort="high", session_id=None):
        return build_launch(
            agent="codex",
            model=model,
            effort=effort,
            prompt="hello",
            cwd=str(self.job),
            job_dir=self.job / "codexshim",
            config={"binaries": {"codex": binary}},
            from_agent="grok",
            conversation="c1",
            depth=1,
            session_id=session_id,
            auto_approve=True,
            allow_missing=True,
        )

    def test_cmd_shim_accepts_normal_codex_argv(self):
        with mock.patch("agents_cli.harness._windows", return_value=True):
            for session in (None, "01a18af0-3006-72b2-8a20-66cb201ca48c"):
                launch = self._codex_with_binary("C:/npm/codex.CMD", session_id=session)
                self.assertIn("model_reasoning_effort=high", launch.argv)
                self.assertIn("gpt-6.1-sol", launch.argv)

    def test_cmd_shim_rejects_dangerous_argument(self):
        with mock.patch("agents_cli.harness._windows", return_value=True):
            for bad in ("a&b", "a|b", "a<b", "a>b", "a^b", "a%PATH%", "a!b", 'a"b', "a\nb", "a\rb"):
                with self.assertRaises(ParseError) as caught:
                    self._codex_with_binary("C:/npm/codex.cmd", model=bad)
                self.assertIn("argumento inseguro para shim .cmd:", str(caught.exception))
            with self.assertRaises(ParseError):
                self._codex_with_binary("C:/npm/codex.bat", effort="high&calc")

    def test_exe_and_non_windows_skip_shim_check(self):
        with mock.patch("agents_cli.harness._windows", return_value=True):
            self._codex_with_binary("C:/codex/codex.exe", model="a&b")
        with mock.patch("agents_cli.harness._windows", return_value=False):
            self._codex_with_binary("C:/npm/codex.cmd", model="a&b")

    def _gemini(self, binary, prompt, name):
        return build_launch(
            agent="gemini",
            model="gemini-3.8-flash",
            effort="high",
            prompt=prompt,
            cwd=str(self.job),
            job_dir=self.job / name,
            config={"binaries": {"gemini": binary}},
            from_agent="claude",
            conversation="c1",
            depth=1,
            session_id=None,
            auto_approve=True,
            allow_missing=True,
        )

    def test_gemini_agy_vs_cli(self):
        agy = self._gemini("C:/agy/agy.exe", "x", "agy")
        self.assertIn("--effort", agy.argv)
        gem = self._gemini("C:/npm/gemini.cmd", "x", "gem")
        self.assertIn("-y", gem.argv)
        self.assertNotIn("--effort", gem.argv)

    def test_gemini_fallback_uses_stdin(self):
        gem = self._gemini("C:/npm/gemini.cmd", "the prompt", "gem")
        self.assertNotIn("-p", gem.argv)
        self.assertNotIn("the prompt", gem.argv)
        self.assertEqual(gem.stdin, "the prompt")

    def test_agy_short_prompt_stays_in_argv(self):
        launch = self._gemini("C:/agy/agy.exe", "short prompt", "agy")
        self.assertIn("short prompt", launch.argv)
        self.assertIsNone(launch.stdin)

    def test_agy_prompt_between_old_and_new_limit_stays_in_argv(self):
        prompt = "y" * 24001
        launch = self._gemini("C:/agy/agy.exe", prompt, "agymid")
        self.assertIn(prompt, launch.argv)

    def test_agy_quote_escaping_counts_toward_argv_limit(self):
        prompt = '"' * 16000  # 16000 chars raw, ~32000 once escaped by list2cmdline
        launch = self._gemini("C:/agy/agy.exe", prompt, "agyquote")
        self.assertNotIn(prompt, launch.argv)
        self.assertIn(str(launch.prompt_file.resolve()), launch.argv[launch.argv.index("-p") + 1])

    def test_agy_non_exe_binary_on_windows_always_uses_pointer(self):
        with mock.patch("agents_cli.harness._windows", return_value=True):
            for name in ("agy.cmd", "agy.bat", "agy.ps1"):
                launch = self._gemini("C:/agy/" + name, "short prompt", "agy" + name[-3:])
                self.assertNotIn("short prompt", launch.argv)
                self.assertIn(str(launch.prompt_file.resolve()), launch.argv[launch.argv.index("-p") + 1])
            launch = self._gemini("C:/agy/agy.exe", "short prompt", "agyexe")
            self.assertIn("short prompt", launch.argv)
        with mock.patch("agents_cli.harness._windows", return_value=False):
            launch = self._gemini("C:/agy/agy.cmd", "short prompt", "agyposix")
            self.assertIn("short prompt", launch.argv)

    def test_agy_long_prompt_points_to_prompt_file(self):
        prompt = "y" * 31000
        launch = self._gemini("C:/agy/agy.exe", prompt, "agylong")
        self.assertNotIn(prompt, launch.argv)
        arg = launch.argv[launch.argv.index("-p") + 1]
        self.assertIn(str(launch.prompt_file.resolve()), arg)
        self.assertLess(len(arg), 1000)
        self.assertEqual(launch.prompt_file.read_text(encoding="utf-8"), prompt)

    def test_agy_prompt_at_limit_stays_in_argv(self):
        prompt = "y" * 29000
        launch = self._gemini("C:/agy/agy.exe", prompt, "agylimit")
        self.assertIn(prompt, launch.argv)

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

    def test_claude_result_type_is_not_codex_stream(self):
        payload = json.dumps({"type": "result", "result": "fine", "is_error": False, "session_id": "s"})
        self.assertFalse(output_is_error(payload))
        text, session = extract_text("claude", payload)
        self.assertEqual(text, "fine")
        self.assertEqual(session, "s")

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

    def test_banner_with_prefix_and_ansi(self):
        stderr = "[2026-10-30T10:00:00] \x1b[1msession id:\x1b[0m 01a15c8b-3006-72b2-8a20-66cb201ca48c\n"
        _, session = extract_text("codex", "ok", stderr=stderr)
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

    CODEX_OK = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "01a15c8b-3006-72b2-8a20-66cb201ca48c"}),
            json.dumps({"type": "turn.started"}),
            json.dumps({"type": "item.completed", "item": {"id": "i0", "type": "reasoning", "text": "think"}}),
            json.dumps({"type": "item.completed", "item": {"id": "i1", "type": "agent_message", "text": "first"}}),
            json.dumps({"type": "item.completed", "item": {"id": "i2", "type": "agent_message", "text": "last one"}}),
            json.dumps({"type": "turn.completed", "usage": {}}),
        ]
    )

    def test_codex_jsonl_text_when_last_message_empty(self):
        path = self.job / "empty.txt"
        path.write_text("", encoding="utf-8")
        text, session = extract_text("codex", self.CODEX_OK, last_message_file=path)
        self.assertEqual(text, "last one")
        self.assertEqual(session, "01a15c8b-3006-72b2-8a20-66cb201ca48c")
        text, _ = extract_text("codex", self.CODEX_OK, last_message_file=self.job / "missing.txt")
        self.assertEqual(text, "last one")
        self.assertFalse(output_is_error(self.CODEX_OK))

    def test_codex_jsonl_failure(self):
        stdout = "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "t1"}),
                json.dumps({"type": "error", "message": "boom"}),
                json.dumps({"type": "turn.failed", "error": {"message": "boom"}}),
            ]
        )
        self.assertTrue(output_is_error(stdout))
        text, _ = extract_text("codex", stdout)
        self.assertEqual(text, "boom")

    def test_codex_transient_error_then_completed_is_not_failure(self):
        stdout = "\n".join(
            [
                json.dumps({"type": "error", "message": "Reconnecting... 1/5"}),
                self.CODEX_OK,
            ]
        )
        self.assertFalse(output_is_error(stdout))

    def test_codex_error_event_alone_is_failure(self):
        self.assertTrue(output_is_error(json.dumps({"type": "error", "message": "x"})))
        self.assertTrue(output_is_error(json.dumps({"type": "error", "message": "x"}), agent="codex"))

    def test_codex_stream_checks_only_apply_to_codex(self):
        stream = json.dumps({"type": "turn.failed", "error": {"message": "boom"}})
        self.assertTrue(output_is_error(stream))
        self.assertTrue(output_is_error(stream, agent="codex"))
        for agent in ("claude", "grok", "gemini"):
            self.assertFalse(output_is_error(stream, agent=agent), agent)
        claude_error = json.dumps({"is_error": True, "result": "x"})
        for agent in ("claude", "grok", "gemini", "codex"):
            self.assertTrue(output_is_error(claude_error, agent=agent), agent)

    def test_non_codex_agent_does_not_use_codex_stream_text(self):
        stream = "\n".join(
            [
                json.dumps({"type": "item.completed", "item": {"id": "i1", "type": "agent_message", "text": "hi"}}),
                json.dumps({"type": "turn.completed"}),
            ]
        )
        text, _ = extract_text("codex", stream)
        self.assertEqual(text, "hi")
        text, _ = extract_text("grok", stream)
        self.assertNotEqual(text, "hi")

    def test_failure_text_prefers_codex_stream_error(self):
        stdout = "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "t1"}),
                json.dumps({"type": "error", "message": "first"}),
                json.dumps({"type": "turn.failed", "error": {"message": "the real reason"}}),
            ]
        )
        self.assertEqual(failure_text("codex", stdout, "noise"), "the real reason")
        self.assertEqual(failure_text("codex", json.dumps({"type": "error", "message": "only"}), ""), "only")
        self.assertEqual(failure_text("codex", "plain text", "stderr"), "")
        self.assertEqual(failure_text("claude", stdout, ""), "")

    def test_resume_missing_patterns(self):
        error = (
            "Error: thread/resume: thread/resume failed: no rollout found for thread id "
            "01a18af0-3006-72b2-8a20-66cb201ca48c (code -32600)"
        )
        self.assertTrue(resume_missing("codex", "", error, ""))
        for phrase in (
            "Session not found",
            "No conversation found with session ID x",
            "conversation not found",
            "Could not find session abc",
            "invalid session id",
            "Thread not found",
        ):
            self.assertTrue(resume_missing("claude", "", phrase, ""), phrase)
        self.assertTrue(resume_missing("grok", "", "No Rollout Found", ""))
        grok_error = (
            'Session "00000000-0000-4000-8000-000000000000" not found locally, restoring conversation from remote...\n'
            "Error: Failed to restore session from remote: fetching session record: session get failed: 404 Not Found"
        )
        self.assertTrue(resume_missing("grok", "", grok_error, ""))
        self.assertTrue(resume_missing("grok", "", 'Session "abc-1" not found locally', ""))
        self.assertFalse(resume_missing("codex", "", "rate limited", "boom"))
        self.assertFalse(resume_missing("codex", None, None, None))

    def test_resume_missing_ignores_phrase_in_plain_stdout_or_text(self):
        self.assertFalse(resume_missing("grok", "session not found", "", ""))
        self.assertFalse(resume_missing("claude", "Session not found", "", ""))
        self.assertFalse(resume_missing("grok", "", "", "...No Rollout Found..."))
        self.assertFalse(resume_missing("codex", "task: check if no rollout found", "", "session not found"))
        message = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "no rollout found"}})
        self.assertFalse(resume_missing("codex", message, "", ""))

    def test_resume_missing_reads_codex_error_events(self):
        error = json.dumps({"type": "error", "message": "no rollout found for thread id x"})
        self.assertTrue(resume_missing("codex", error, "", ""))
        failed = json.dumps({"type": "turn.failed", "error": {"message": "Thread not found"}})
        self.assertTrue(resume_missing("codex", failed, "", ""))
        self.assertFalse(resume_missing("claude", error, "", ""))


class RunLaunchTests(unittest.TestCase):
    def _launch(self, argv, stdin=None):
        return Launch("claude", argv, str(Path.cwd()), dict(os.environ), Path("p.txt"), stdin=stdin)

    def _fake_popen(self):
        proc = mock.MagicMock()
        proc.pid = 4242
        proc.returncode = 0
        proc.communicate.return_value = ("out", "err")
        return proc

    def test_devnull_stdin_when_no_prompt(self):
        proc = self._fake_popen()
        with mock.patch("agents_cli.harness.subprocess.Popen", return_value=proc) as popen:
            result = run_launch(self._launch(["x"]), timeout=5)
        self.assertEqual(popen.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(result.stdout, "out")
        self.assertEqual(result.returncode, 0)

    def test_pipe_stdin_when_prompt(self):
        proc = self._fake_popen()
        with mock.patch("agents_cli.harness.subprocess.Popen", return_value=proc) as popen:
            run_launch(self._launch(["x"], stdin="hi"), timeout=5)
        self.assertEqual(popen.call_args.kwargs["stdin"], subprocess.PIPE)
        self.assertEqual(proc.communicate.call_args_list[0].kwargs["input"], "hi")

    def test_timeout_kills_tree_and_reraises(self):
        proc = self._fake_popen()
        proc.communicate.side_effect = [subprocess.TimeoutExpired("x", 1), ("", "")]
        with mock.patch("agents_cli.harness.subprocess.Popen", return_value=proc):
            with mock.patch("agents_cli.harness.procs.kill_tree") as kill:
                with self.assertRaises(subprocess.TimeoutExpired):
                    run_launch(self._launch(["x"]), timeout=1)
        kill.assert_called_once_with(4242)

    def test_keyboard_interrupt_kills_tree_and_reraises(self):
        proc = self._fake_popen()
        proc.communicate.side_effect = [KeyboardInterrupt(), ("", "")]
        with mock.patch("agents_cli.harness.subprocess.Popen", return_value=proc):
            with mock.patch("agents_cli.harness.procs.kill_tree") as kill:
                with self.assertRaises(KeyboardInterrupt):
                    run_launch(self._launch(["x"]), timeout=1)
        kill.assert_called_once_with(4242)

    def test_real_quick_run(self):
        launch = self._launch([sys.executable, "-c", "print('ok')"])
        result = run_launch(launch, timeout=30)
        self.assertEqual(result.stdout.strip(), "ok")
        self.assertEqual(result.returncode, 0)

    def test_stale_last_message_is_removed_before_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            last = Path(tmp) / "last_message.txt"
            last.write_text("stale answer", encoding="utf-8")
            # Exit 0 only if the file is already gone when the process starts.
            script = "import os, sys; sys.exit(1 if os.path.exists(sys.argv[1]) else 0)"
            launch = self._launch([sys.executable, "-c", script, str(last)])
            launch.last_message_file = last
            result = run_launch(launch, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(last.exists())

    def test_missing_last_message_file_is_fine(self):
        with tempfile.TemporaryDirectory() as tmp:
            launch = self._launch([sys.executable, "-c", "print('ok')"])
            launch.last_message_file = Path(tmp) / "absent.txt"
            self.assertEqual(run_launch(launch, timeout=30).returncode, 0)

    def test_real_stdin_roundtrip(self):
        launch = self._launch([sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"], stdin="abc")
        result = run_launch(launch, timeout=30)
        self.assertEqual(result.stdout.strip(), "ABC")

    def test_real_timeout_is_fast(self):
        launch = self._launch([sys.executable, "-c", "import time; time.sleep(30)"])
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            run_launch(launch, timeout=1)
        self.assertLess(time.monotonic() - started, 10)
