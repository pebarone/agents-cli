import io
import json
import argparse
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from agents_cli.bus import job_dir as job_dir_path
from agents_cli.bus import (
    append_message,
    create_job,
    get_or_create_conversation,
    list_jobs,
    load_conversation,
    load_job,
    read_messages,
    remember_session,
    session_record,
    update_job,
)
from agents_cli import cli as cli_mod
from agents_cli.cli import _plan_and_claim, main, spawn_detached
from agents_cli.harness import Launch
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
        for name in ("AGENTS_SELF", "AGENTS_FROM", "AGENTS_DEPTH"):
            os.environ.pop(name, None)
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


def dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def fake_build_launch(**kwargs):
    folder = kwargs["job_dir"]
    return Launch(
        agent=kwargs["agent"],
        argv=["fake", kwargs["agent"]],
        cwd=kwargs["cwd"],
        env={},
        prompt_file=folder / "prompt.txt",
    )


def harness(run_launch, text="hello", session="sess-1"):
    """Patch the harness layer so no real agent is started."""
    stack = ExitStack()
    stack.enter_context(mock.patch("agents_cli.cli.which", return_value="fake"))
    stack.enter_context(mock.patch("agents_cli.cli.build_launch", side_effect=fake_build_launch))
    stack.enter_context(mock.patch("agents_cli.cli.run_launch", run_launch))
    stack.enter_context(mock.patch("agents_cli.cli.extract_text", return_value=(text, session)))
    return stack


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(["fake"], returncode, stdout, stderr)


ENV_NAMES = ("AGENTS_HOME", "AGENTS_SELF", "AGENTS_FROM", "AGENTS_CONVERSATION", "AGENTS_DEPTH")


class BusEnvCase(unittest.TestCase):
    self_agent = "grok"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTS_HOME"] = self.tmp.name
        os.environ["AGENTS_SELF"] = self.self_agent

    def tearDown(self):
        for name in ENV_NAMES:
            os.environ.pop(name, None)
        self.tmp.cleanup()


class LifecycleTests(BusEnvCase):
    def test_success_marks_job_ok_and_records_reply(self):
        with harness(mock.Mock(return_value=completed(0, "out"))):
            code, out, err = run_main(["delegate", "claude", "--json", "--prompt", "do it"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["status"], "ok")
        job = load_job(data["job"])
        self.assertEqual(job["status"], "ok")
        self.assertEqual(job["text"], "hello")
        self.assertEqual(job["pid"], os.getpid())
        self.assertIn("started_at", job)
        kinds = [m["kind"] for m in read_messages(data["conversation"])]
        self.assertEqual(kinds, ["delegate", "reply"])
        meta = load_conversation(data["conversation"])
        self.assertEqual(session_record(meta, "claude")["id"], "sess-1")

    def test_timeout_marks_job_and_appends_error_message(self):
        run = mock.Mock(side_effect=subprocess.TimeoutExpired(["fake"], 1))
        with harness(run):
            code, out, err = run_main(["delegate", "claude", "--prompt", "slow"])
        self.assertEqual(code, 3)
        self.assertIn("tempo limite", err)
        job = list_jobs()[0]
        self.assertEqual(job["status"], "timeout")
        self.assertEqual(job["returncode"], 3)
        last = read_messages(job["conversation"])[-1]
        self.assertEqual((last["kind"], last["from"], last["to"], last["job"]), ("error", "claude", "grok", job["id"]))
        self.assertTrue(last["text"])

    def test_cancelled_marks_job(self):
        with harness(mock.Mock(side_effect=KeyboardInterrupt)):
            code, _, _ = run_main(["delegate", "claude", "--prompt", "x"])
        self.assertEqual(code, 130)
        self.assertEqual(list_jobs()[0]["status"], "cancelled")

    def test_unexpected_launch_error_does_not_leave_job_running(self):
        with harness(mock.Mock(side_effect=OSError("boom"))):
            code, _, err = run_main(["delegate", "claude", "--prompt", "x"])
        self.assertEqual(code, 2)
        self.assertIn("boom", err)
        self.assertEqual(list_jobs()[0]["status"], "error")

    def test_json_on_failure_prints_json(self):
        with harness(mock.Mock(return_value=completed(1, "", "bad")), text="boom", session=None):
            code, out, err = run_main(["delegate", "claude", "--json", "--prompt", "x"])
        self.assertEqual(code, 1)
        data = json.loads(out)
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["returncode"], 1)
        self.assertIn("boom", data["error"])
        self.assertTrue(data["conversation"] and data["job"])
        self.assertEqual(load_job(data["job"])["status"], "error")

    def test_json_on_timeout_prints_json(self):
        with harness(mock.Mock(side_effect=subprocess.TimeoutExpired(["fake"], 1))):
            code, out, _ = run_main(["delegate", "claude", "--json", "--prompt", "x"])
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(out)["status"], "timeout")

    def test_busy_session_forces_fresh_session(self):
        meta = get_or_create_conversation("cbusy0000001", os.getcwd(), "grok")
        remember_session(meta, "claude", "sess-abc", model="sonnet", effort="high")
        create_job({"agent": "claude", "conversation": "cbusy0000001", "status": "running", "pid": os.getpid()})
        code, out, err = run_main(
            ["delegate", "claude", "--conversation", "cbusy0000001", "--dry-run", "--json", "--prompt", "again"]
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertIsNone(data["resume"])
        self.assertNotIn("sess-abc", data["argv"])
        self.assertIn("[agents] aviso:", err)

    def test_dead_running_job_does_not_block_resume(self):
        meta = get_or_create_conversation("cbusy0000002", os.getcwd(), "grok")
        remember_session(meta, "claude", "sess-abc")
        create_job({"agent": "claude", "conversation": "cbusy0000002", "status": "running", "pid": dead_pid()})
        code, out, _ = run_main(
            ["delegate", "claude", "--conversation", "cbusy0000002", "--dry-run", "--json", "--prompt", "again"]
        )
        self.assertEqual(json.loads(out)["resume"], "sess-abc")

    def test_self_session_in_same_conversation_is_not_resumed(self):
        meta = get_or_create_conversation("cself0000001", os.getcwd(), "grok")
        remember_session(meta, "claude", "sess-abc")
        os.environ["AGENTS_SELF"] = "claude"
        os.environ["AGENTS_CONVERSATION"] = "cself0000001"
        code, out, err = run_main(["delegate", "claude", "--dry-run", "--json", "--prompt", "me again"])
        self.assertEqual(code, 0)
        self.assertIsNone(json.loads(out)["resume"])
        self.assertIn("aviso", err)

    def test_missing_conversation_is_created_with_warning(self):
        code, out, err = run_main(
            ["delegate", "claude", "--conversation", "cnew00000001", "--dry-run", "--json", "--prompt", "x"]
        )
        self.assertEqual(code, 0)
        self.assertIn("[agents] aviso: conversa cnew00000001 nao existia; criada agora", err)
        self.assertIsNotNone(load_conversation("cnew00000001"))

    def test_new_sender_is_added_to_participants(self):
        get_or_create_conversation("cpart0000001", os.getcwd(), "grok")
        os.environ["AGENTS_SELF"] = "gemini"
        code, _, err = run_main(
            ["delegate", "claude", "--conversation", "cpart0000001", "--dry-run", "--json", "--prompt", "x"]
        )
        self.assertEqual(code, 0)
        self.assertNotIn("nao existia", err)
        self.assertEqual(load_conversation("cpart0000001")["participants"], ["grok", "gemini"])

    def test_bad_conversation_id_is_a_clean_error(self):
        code, _, err = run_main(["delegate", "claude", "--conversation", "../x", "--dry-run", "--prompt", "x"])
        self.assertEqual(code, 2)
        self.assertIn("erro:", err)

    def test_main_catches_value_runtime_and_os_errors(self):
        for exc in (ValueError("config invalido"), RuntimeError("lock"), OSError("disco")):
            with mock.patch("agents_cli.cli.load_config", side_effect=exc):
                code, _, err = run_main(["list"])
            self.assertEqual(code, 2)
            self.assertIn(f"erro: {exc}", err)

    def test_free_text_task_is_not_eaten_as_model(self):
        code, out, _ = run_main(["delegate", "codex", "fix", "the", "bug", "--dry-run", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertNotEqual(data["model"], "fix")
        self.assertIn("fix the bug", Path(data["prompt_file"]).read_text(encoding="utf-8"))


class AtomicClaimTests(BusEnvCase):
    def test_parallel_plan_and_claim_resumes_session_only_once(self):
        for round_ in range(5):
            cid = f"cclaim{round_:06d}"
            meta = get_or_create_conversation(cid, os.getcwd(), "grok")
            remember_session(meta, "claude", "sess-abc", model="sonnet", effort="high")
            barrier = threading.Barrier(2)
            results = []
            errors = []

            def attempt():
                try:
                    args = argparse.Namespace(no_resume=False, new=False)
                    request = cli_mod._Req("claude", None, None, "again")
                    fields = {"from": "grok", "conversation": cid, "cwd": os.getcwd(), "depth": 1}
                    barrier.wait(timeout=10)
                    results.append(_plan_and_claim(args, meta, request, {}, None, fields))
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

            threads = [threading.Thread(target=attempt) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
            self.assertEqual(errors, [])
            self.assertEqual(len(results), 2)
            resumed = [r for r in results if r[0] == "sess-abc"]
            fresh = [r for r in results if r[0] is None]
            self.assertEqual((len(resumed), len(fresh)), (1, 1), round_)
            self.assertIn("ocupada", fresh[0][3])
            for r in results:
                self.assertEqual(r[4]["status"], "running")
                self.assertEqual(r[4]["pid"], os.getpid())

    def test_claim_marks_existing_child_job_running(self):
        meta = get_or_create_conversation("cclaim100000", os.getcwd(), "grok")
        job = create_job({"agent": "claude", "conversation": "cclaim100000", "status": "queued"})
        args = argparse.Namespace(no_resume=False, new=False)
        request = cli_mod._Req("claude", None, None, "x")
        fields = {"from": "grok", "conversation": "cclaim100000", "cwd": os.getcwd(), "depth": 1}
        *_, claimed = _plan_and_claim(args, meta, request, {}, job["id"], fields)
        self.assertEqual((claimed["status"], claimed["pid"]), ("running", os.getpid()))
        with self.assertRaises(cli_mod._ClaimLost):
            _plan_and_claim(args, meta, request, {}, job["id"], fields)


RESUME_ERROR = (
    "Error: thread/resume: thread/resume failed: no rollout found for thread id "
    "01a18af0-3006-72b2-8a20-66cb201ca48c (code -32600)"
)
NEW_THREAD = "01bbbbbb-3006-72b2-8a20-66cb201ca48c"
CODEX_OK_STREAM = "\n".join(
    [
        json.dumps({"type": "thread.started", "thread_id": NEW_THREAD}),
        json.dumps({"type": "item.completed", "item": {"id": "i1", "type": "agent_message", "text": "fresh answer"}}),
        json.dumps({"type": "turn.completed", "usage": {}}),
    ]
)


class ResumeFallbackTests(BusEnvCase):
    cid = "cresume00001"
    old_session = "01a18af0-3006-72b2-8a20-66cb201ca48c"

    def setUp(self):
        super().setUp()
        meta = get_or_create_conversation(self.cid, os.getcwd(), "grok")
        remember_session(meta, "codex", self.old_session, model="gpt-6.1-sol", effort="high")
        append_message(self.cid, {"from": "grok", "to": "codex", "kind": "delegate", "job": "jold", "text": "first question"})
        append_message(self.cid, {"from": "codex", "to": "grok", "kind": "reply", "job": "jold", "text": "first answer"})

    def run_codex(self, outcomes, extra=()):
        run = mock.Mock(side_effect=outcomes)
        with mock.patch("agents_cli.cli.which", return_value="codex"), mock.patch(
            "agents_cli.harness.which", return_value="codex"
        ), mock.patch("agents_cli.cli.run_launch", run):
            result = run_main(
                ["delegate", "codex", "--conversation", self.cid, "--json", *extra, "--prompt", "second question"]
            )
        return run, result

    def test_missing_rollout_retries_with_new_session_and_history(self):
        run, (code, out, err) = self.run_codex(
            [completed(1, "", RESUME_ERROR), completed(0, CODEX_OK_STREAM, "")]
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(run.call_count, 2)
        first, second = (call.args[0] for call in run.call_args_list)
        self.assertIn("resume", first.argv)
        self.assertIn(self.old_session, first.argv)
        self.assertIn(f"resume: {self.old_session}", first.stdin)
        self.assertNotIn("first question", first.stdin)
        self.assertNotIn("resume", second.argv)
        self.assertNotIn(self.old_session, second.argv)
        self.assertIn("resume: new", second.stdin)
        self.assertIn("first question", second.stdin)
        self.assertIn("first answer", second.stdin)
        self.assertEqual(second.stdin.count("second question"), 1)
        self.assertIn(
            f"[agents] aviso: sessao {self.old_session} do codex nao existe mais; abrindo sessao nova com historico",
            err,
        )
        data = json.loads(out)
        self.assertEqual(data["status"], "ok")
        self.assertFalse(data["resumed"])
        self.assertEqual(data["text"], "fresh answer")
        job = load_job(data["job"])
        self.assertEqual((job["status"], job["resume_failed"]), ("ok", self.old_session))
        self.assertEqual(session_record(load_conversation(self.cid), "codex")["id"], NEW_THREAD)
        replies = [m for m in read_messages(self.cid) if m.get("kind") == "reply" and m.get("job") == data["job"]]
        self.assertEqual(len(replies), 1)

    def test_only_one_retry(self):
        run, (code, out, err) = self.run_codex([completed(1, "", RESUME_ERROR), completed(1, "", RESUME_ERROR)])
        self.assertEqual(code, 1)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(json.loads(out)["status"], "error")
        self.assertIsNone(session_record(load_conversation(self.cid), "codex").get("id"))

    def test_other_failures_do_not_retry_or_forget_session(self):
        run, (code, out, err) = self.run_codex([completed(1, "", "Error: rate limited")])
        self.assertEqual(code, 1)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(session_record(load_conversation(self.cid), "codex")["id"], self.old_session)

    def test_fresh_session_failure_never_retries(self):
        run, (code, _, _) = self.run_codex([completed(1, "", RESUME_ERROR)], extra=("--no-resume",))
        self.assertEqual(code, 1)
        self.assertEqual(run.call_count, 1)

    def test_codex_failure_detail_prefers_stream_error(self):
        stream = "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "t1"}),
                json.dumps({"type": "turn.failed", "error": {"message": "model gpt-9 is not supported"}}),
            ]
        )
        run, (code, out, err) = self.run_codex([completed(1, stream, "banner noise")])
        self.assertEqual(code, 1)
        self.assertIn("model gpt-9 is not supported", json.loads(out)["error"])

    def test_codex_dry_run_has_no_cwd_flag(self):
        code, out, _ = run_main(["delegate", "codex", "--dry-run", "--json", "--prompt", "teste"])
        self.assertEqual(code, 0)
        argv = json.loads(out)["argv"]
        self.assertNotIn("-C", argv)
        self.assertIn("exec", argv)


class ReplyTests(BusEnvCase):
    self_agent = "claude"

    def setUp(self):
        super().setUp()
        meta = get_or_create_conversation("creply000001", os.getcwd(), "grok")
        remember_session(meta, "claude", "sess-1")

    def test_refuses_to_reply_to_the_agent_waiting_on_you(self):
        os.environ["AGENTS_FROM"] = "grok"
        os.environ["AGENTS_CONVERSATION"] = "creply000001"
        code, _, err = run_main(["reply", "--conversation", "creply000001", "--prompt", "done"])
        self.assertEqual(code, 2)
        self.assertIn("erro: sua resposta final ja volta para grok automaticamente; so imprima a resposta", err)

    def test_reply_in_another_conversation_is_allowed(self):
        os.environ["AGENTS_FROM"] = "grok"
        os.environ["AGENTS_CONVERSATION"] = "cother0000001"
        code, out, _ = run_main(["reply", "--conversation", "creply000001", "--prompt", "hi", "--dry-run", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["agent"], "grok")

    def test_prompt_starting_with_dash(self):
        code, out, _ = run_main(
            ["reply", "--conversation", "creply000001", "--prompt=-flag first", "--dry-run", "--json"]
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertIn("-flag first", Path(data["prompt_file"]).read_text(encoding="utf-8"))

    def test_unknown_conversation_is_an_error_and_not_created(self):
        code, _, err = run_main(["reply", "--conversation", "cmissing0001", "--prompt", "x", "--to", "grok"])
        self.assertEqual(code, 2)
        self.assertIn("nao encontrada", err)
        self.assertIsNone(load_conversation("cmissing0001"))


class BackgroundTests(BusEnvCase):
    def patched_spawn(self, spawn):
        stack = ExitStack()
        stack.enter_context(mock.patch("agents_cli.cli.which", return_value="fake"))
        stack.enter_context(mock.patch("agents_cli.cli.spawn_detached", spawn))
        return stack

    def test_background_spawns_detached_child_with_expected_argv(self):
        spawn = mock.Mock(return_value=4242)
        with self.patched_spawn(spawn):
            code, out, err = run_main(
                ["delegate", "claude", "sonnet", "high", "--background", "--prompt", "long task", "--timeout", "77"]
            )
        self.assertEqual(code, 0)
        jid = out.strip()
        job = load_job(jid)
        self.assertEqual(job["status"], "queued")
        cid = job["conversation"]
        self.assertIn(f"[agents] conversation={cid} job={jid} background=yes", err)
        spawn.assert_called_once()
        argv, log_path = spawn.call_args.args[0], spawn.call_args.args[1]
        self.assertEqual(argv[:3], [sys.executable, "-u", str(Path(__file__).resolve().parent.parent / "agents.py")])
        self.assertEqual(argv[3:5], ["delegate", "claude"])
        for flag, value in (
            ("--job", jid),
            ("--conversation", cid),
            ("--from", "grok"),
            ("--model", "sonnet"),
            ("--effort", "high"),
            ("--timeout", "77"),
        ):
            self.assertEqual(argv[argv.index(flag) + 1], value, flag)
        prompt_file = Path(argv[argv.index("--prompt-file") + 1])
        self.assertEqual(prompt_file.read_text(encoding="utf-8"), "long task")
        self.assertEqual(job["launcher_pid"], 4242)
        self.assertEqual(Path(log_path).name, "agents.log")
        self.assertEqual(Path(log_path).parent, prompt_file.parent)
        self.assertEqual([m["kind"] for m in read_messages(cid)], ["delegate"])

    def test_background_json_and_flags(self):
        spawn = mock.Mock(return_value=1)
        with self.patched_spawn(spawn):
            code, out, _ = run_main(
                ["delegate", "codex", "--background", "--new", "--no-history", "--json", "--prompt", "x"]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["status"], "queued")
        argv = spawn.call_args.args[0]
        self.assertIn("--new", argv)
        self.assertIn("--no-history", argv)
        self.assertNotIn("--no-resume", argv)

    def test_child_reuses_the_job_created_by_parent(self):
        spawn = mock.Mock(return_value=1)
        with self.patched_spawn(spawn):
            run_main(["delegate", "claude", "--background", "--prompt", "work"])
        argv = spawn.call_args.args[0]
        child_args = argv[argv.index("delegate"):]
        jid = child_args[child_args.index("--job") + 1]
        with harness(mock.Mock(return_value=completed(0, "out"))):
            code, out, _ = run_main(child_args)
        self.assertEqual(code, 0)
        self.assertEqual(len(list_jobs()), 1)
        job = load_job(jid)
        self.assertEqual(job["status"], "ok")
        self.assertEqual(job["text"], "hello")
        kinds = [m["kind"] for m in read_messages(job["conversation"])]
        self.assertEqual(kinds, ["delegate", "reply"])

    def test_spawn_failure_marks_job_error(self):
        spawn = mock.Mock(side_effect=OSError("no spawn"))
        with self.patched_spawn(spawn):
            code, _, err = run_main(["delegate", "claude", "--background", "--prompt", "x"])
        self.assertEqual(code, 2)
        self.assertEqual(list_jobs()[0]["status"], "error")


    def _queue_child(self):
        spawn = mock.Mock(return_value=4242)
        with self.patched_spawn(spawn):
            run_main(["delegate", "claude", "--background", "--prompt", "work"])
        argv = spawn.call_args.args[0]
        child_args = argv[argv.index("delegate"):]
        return child_args, child_args[child_args.index("--job") + 1]

    def test_child_exits_without_touching_job_when_claim_is_lost(self):
        child_args, jid = self._queue_child()
        update_job(jid, {"status": "error", "error": "processo do agente morreu"})
        run = mock.Mock(return_value=completed(0, "out"))
        with harness(run):
            code, out, err = run_main(child_args)
        self.assertEqual(code, 1)
        run.assert_not_called()
        job = load_job(jid)
        self.assertEqual(job["status"], "error")
        self.assertEqual(job["error"], "processo do agente morreu")
        self.assertNotIn("pid", job)
        self.assertEqual([m["kind"] for m in read_messages(job["conversation"])], ["delegate"])

    def test_child_setup_failure_finalizes_job_with_real_error(self):
        child_args, jid = self._queue_child()
        with mock.patch("agents_cli.cli.which", return_value=None):
            code, _, err = run_main(child_args)
        self.assertEqual(code, 2)
        job = load_job(jid)
        self.assertEqual(job["status"], "error")
        self.assertIn("nao encontrado", job["error"])
        self.assertEqual(read_messages(job["conversation"])[-1]["kind"], "error")

    def test_child_depth_overflow_finalizes_job(self):
        child_args, jid = self._queue_child()
        os.environ["AGENTS_DEPTH"] = "50"
        with harness(mock.Mock()):
            code, _, err = run_main(child_args)
        self.assertEqual(code, 4)
        job = load_job(jid)
        self.assertEqual(job["status"], "error")
        self.assertIn("profundidade maxima", job["error"])

    def test_dead_launcher_makes_queued_job_stale_for_wait(self):
        child_args, jid = self._queue_child()
        update_job(jid, {"launcher_pid": dead_pid()})
        code, _, err = run_main(["wait", jid, "--interval", "0.05", "--timeout", "5"])
        self.assertEqual(code, 1)
        self.assertEqual(load_job(jid)["status"], "error")


class SpawnTests(unittest.TestCase):
    def _spawn(self, popen):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(cli_mod.os, "name", "nt"):
                with mock.patch.object(cli_mod.subprocess, "Popen", popen):
                    return spawn_detached(["x"], Path(tmp) / "agents.log", tmp)

    def test_windows_flags_hide_window_and_keep_group_and_breakaway(self):
        popen = mock.Mock(return_value=mock.Mock(pid=77))
        self.assertEqual(self._spawn(popen), 77)
        flags = popen.call_args.kwargs["creationflags"]
        self.assertEqual(
            flags,
            subprocess.CREATE_NEW_PROCESS_GROUP | 0x08000000 | subprocess.CREATE_BREAKAWAY_FROM_JOB,
        )
        self.assertFalse(flags & 0x00000008)  # not DETACHED_PROCESS

    def test_breakaway_denied_retries_without_it(self):
        popen = mock.Mock(side_effect=[OSError("denied"), mock.Mock(pid=78)])
        self.assertEqual(self._spawn(popen), 78)
        self.assertEqual(popen.call_count, 2)
        retry = popen.call_args_list[1].kwargs["creationflags"]
        self.assertEqual(retry, subprocess.CREATE_NEW_PROCESS_GROUP | 0x08000000)


class WaitTests(BusEnvCase):
    def test_wait_returns_text_once_job_flips_to_ok(self):
        job = create_job({"agent": "claude", "status": "queued"})
        timer = threading.Timer(0.3, update_job, args=(job["id"], {"status": "ok", "text": "the answer"}))
        timer.start()
        code, out, _ = run_main(["wait", job["id"], "--interval", "0.05", "--timeout", "10"])
        timer.join()
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "the answer")

    def test_wait_on_dead_pid_marks_error(self):
        job = create_job({"agent": "claude", "status": "running", "pid": dead_pid()})
        code, out, err = run_main(["wait", job["id"], "--interval", "0.05", "--timeout", "5"])
        self.assertEqual(code, 1)
        self.assertIn("processo do agente morreu", err)
        reloaded = load_job(job["id"])
        self.assertEqual(reloaded["status"], "error")
        self.assertEqual(reloaded["error"], "processo do agente morreu")

    def test_wait_times_out_with_exit_3(self):
        job = create_job({"agent": "claude", "status": "running", "pid": os.getpid()})
        code, _, err = run_main(["wait", job["id"], "--interval", "0.05", "--timeout", "1"])
        self.assertEqual(code, 3)
        self.assertEqual(load_job(job["id"])["status"], "running")

    def test_wait_several_jobs_prints_blocks(self):
        a = create_job({"agent": "claude", "status": "ok", "text": "alpha"})
        b = create_job({"agent": "codex", "status": "error", "error": "kaput"})
        code, out, _ = run_main(["wait", a["id"], b["id"], "--interval", "0.05"])
        self.assertEqual(code, 1)
        self.assertIn(f"=== claude {a['id']} (ok) ===\nalpha", out)
        self.assertIn(f"=== codex {b['id']} (error) ===", out)
        self.assertIn("kaput", out)

    def test_wait_json_is_a_list(self):
        a = create_job({"agent": "claude", "status": "ok", "text": "alpha"})
        code, out, _ = run_main(["wait", a["id"], "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data[0]["job"], a["id"])
        self.assertEqual(data[0]["text"], "alpha")

    def test_wait_corrupt_meta_is_final_even_with_zero_timeout(self):
        job = create_job({"agent": "claude", "status": "running", "pid": os.getpid()})
        (job_dir_path(job["id"]) / "meta.json").write_text("{not json", encoding="utf-8")
        code, out, err = run_main(["wait", job["id"], "--timeout", "0", "--json"])
        self.assertEqual(code, 1)
        data = json.loads(out)
        self.assertEqual((data[0]["job"], data[0]["status"]), (job["id"], "corrupt"))

    def test_wait_corrupt_meta_after_grace_period(self):
        job = create_job({"agent": "claude", "status": "running", "pid": os.getpid()})
        (job_dir_path(job["id"]) / "meta.json").write_text("", encoding="utf-8")
        with mock.patch.object(cli_mod, "CORRUPT_GRACE_SEC", 0.2):
            code, out, err = run_main(["wait", job["id"], "--interval", "0.05", "--timeout", "30"])
        self.assertEqual(code, 1)
        self.assertIn("corrupt", err)

    def test_wait_transient_unreadable_meta_recovers(self):
        job = create_job({"agent": "claude", "status": "running", "pid": os.getpid()})
        path = job_dir_path(job["id"]) / "meta.json"
        path.write_text("{", encoding="utf-8")
        good = json.dumps({"id": job["id"], "agent": "claude", "status": "ok", "text": "fine"})
        timer = threading.Timer(0.3, path.write_text, args=(good,), kwargs={"encoding": "utf-8"})
        timer.start()
        code, out, _ = run_main(["wait", job["id"], "--interval", "0.05", "--timeout", "10"])
        timer.join()
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "fine")

    def test_wait_meta_vanishing_is_corrupt_immediately(self):
        calls = []

        def fake_load(jid):
            calls.append(jid)
            if len(calls) == 1:
                return {"id": jid, "status": "running", "pid": os.getpid()}
            raise FileNotFoundError(jid)

        with mock.patch.object(cli_mod, "load_job", side_effect=fake_load):
            code, out, _ = run_main(["wait", "jvanish0001", "--interval", "0.05", "--timeout", "30", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)[0]["status"], "corrupt")

    def test_wait_unknown_job(self):
        code, _, err = run_main(["wait", "jnope00000"])
        self.assertEqual(code, 2)
        self.assertIn("job nao encontrado", err)


class ReportingTests(BusEnvCase):
    def test_status_shows_latest_conversations_in_order_and_stale_jobs(self):
        for i in range(12):
            get_or_create_conversation(f"cstat{i:07d}", os.getcwd(), "grok")
        create_job({"id": "jstale00001", "agent": "claude", "from": "grok", "status": "running", "pid": dead_pid()})
        create_job({"id": "jlive000001", "agent": "claude", "from": "grok", "status": "running", "pid": os.getpid()})
        code, out, _ = run_main(["status"])
        self.assertEqual(code, 0)
        convo_lines = [line for line in out.splitlines() if line.startswith("  cstat")]
        self.assertEqual(len(convo_lines), 10)
        self.assertIn("cstat0000011", convo_lines[-1])
        self.assertNotIn("cstat0000000", out)
        stale_line = next(line for line in out.splitlines() if "jstale00001" in line)
        live_line = next(line for line in out.splitlines() if "jlive000001" in line)
        self.assertIn("stale", stale_line)
        self.assertIn("running", live_line)

    def test_log_tail_and_json(self):
        get_or_create_conversation("clog00000001", os.getcwd(), "grok")
        for i in range(5):
            append_message("clog00000001", {"from": "grok", "text": f"m{i}"})
        code, out, _ = run_main(["log", "clog00000001", "--tail", "2"])
        self.assertIn("m4", out)
        self.assertIn("m3", out)
        self.assertNotIn("m2", out)
        code, out, _ = run_main(["log", "clog00000001", "--tail", "2", "--json"])
        self.assertEqual([m["text"] for m in json.loads(out)], ["m3", "m4"])
        code, out, _ = run_main(["log", "--json"])
        self.assertEqual(json.loads(out)[0]["id"], "clog00000001")

    def test_inbox_human_and_json(self):
        run_main(["send", "claude", "--prompt", "ping"])
        code, out, _ = run_main(["inbox", "--agent", "claude"])
        self.assertRegex(out, r"^\[\d{4}-.*\] grok -> claude: ping")
        code, out, _ = run_main(["inbox", "--agent", "claude", "--json"])
        rows = json.loads(out)
        self.assertEqual(rows[0]["text"], "ping")
        self.assertTrue(rows[0]["id"])

    def test_doctor_prints_codex_chrome_rows_without_changing_exit_code(self):
        rows = [
            {"name": "extension", "status": "warn", "detail": "nao achei"},
            {"name": "host", "status": "fail", "detail": "quebrado"},
        ]
        with mock.patch("agents_cli.cli.codex_chrome.diagnose", return_value=[]):
            base, _, _ = run_main(["doctor"])
        with mock.patch("agents_cli.cli.codex_chrome.diagnose", return_value=rows):
            code, out, _ = run_main(["doctor"])
        self.assertEqual(code, base)
        self.assertIn("codex (chrome e sessoes):", out)
        self.assertNotIn("codex chrome:", out)
        self.assertIn("  warn     extension: nao achei", out)
        self.assertIn("  fail     host: quebrado", out)
