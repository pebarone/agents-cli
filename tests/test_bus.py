import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from agents_cli.bus import (
    add_participant,
    append_message,
    conversation_dir,
    create_job,
    enqueue_inbox,
    file_lock,
    forget_session,
    get_or_create_conversation,
    job_dir,
    job_is_stale,
    list_conversations,
    list_jobs,
    load_conversation,
    load_job,
    read_inbox,
    read_messages,
    reap_job,
    remember_session,
    running_jobs,
    session_record,
    update_job,
    write_json,
)
from agents_cli.parse import ParseError


def dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


STARTED = "2026-10-30T12:00:00Z"
STARTED_EPOCH = 1793361600  # calendar.timegm of STARTED


def filetime(epoch: float) -> int:
    """Windows FILETIME ticks for a Unix epoch (what process_creation_time returns)."""
    return int((epoch + 11644473600) * 10_000_000)


class BusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTS_HOME"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("AGENTS_HOME", None)
        self.tmp.cleanup()

    def test_conversation_roundtrip(self):
        meta = get_or_create_conversation(None, str(Path.cwd()), "grok")
        cid = meta["id"]
        append_message(cid, {"from": "grok", "to": "claude", "text": "hi"})
        append_message(cid, {"from": "claude", "to": "grok", "text": "yo"})
        messages = read_messages(cid)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[1]["text"], "yo")
        remember_session(meta, "claude", "sess-1", model="sonnet", effort="xhigh")
        again = get_or_create_conversation(cid, str(Path.cwd()), "grok")
        rec = session_record(again, "claude")
        self.assertEqual(rec["id"], "sess-1")
        self.assertEqual(rec["model"], "sonnet")
        self.assertEqual(rec["effort"], "xhigh")

    def test_session_record_legacy_string(self):
        rec = session_record({"sessions": {"grok": "old-id"}}, "grok")
        self.assertEqual(rec, {"id": "old-id"})

    def test_inbox(self):
        enqueue_inbox("gemini", {"text": "task"})
        rows = read_inbox("gemini", consume=True)
        self.assertEqual(rows[0]["text"], "task")
        self.assertEqual(read_inbox("gemini"), [])

    def test_job(self):
        job = create_job({"agent": "codex", "status": "queued"})
        self.assertTrue(job["id"].startswith("j") or len(job["id"]) > 4)
        self.assertEqual(job["status"], "queued")

    def test_ids_are_validated_as_path_components(self):
        for bad in ("../x", "..\\x", "a/b", "", ".hidden", "x" * 65, "a b"):
            with self.assertRaises(ParseError, msg=bad):
                conversation_dir(bad)
            with self.assertRaises(ParseError, msg=bad):
                job_dir(bad)
        with self.assertRaises(ParseError):
            read_inbox("../x")
        with self.assertRaises(ParseError):
            enqueue_inbox("../x", {"text": "t"})
        with self.assertRaises(ParseError):
            get_or_create_conversation("../x", ".", "grok")
        self.assertTrue(conversation_dir("c-ok_1").is_dir())

    def test_concurrent_appends_are_all_valid_lines(self):
        cid = get_or_create_conversation(None, ".", "grok")["id"]

        def worker(tag):
            for i in range(200):
                append_message(cid, {"from": tag, "text": f"{tag}-{i}"})

        threads = [threading.Thread(target=worker, args=(t,)) for t in ("a", "b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        lines = (conversation_dir(cid) / "messages.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 400)
        for line in lines:
            json.loads(line)
        self.assertEqual(len(read_messages(cid)), 400)

    def test_lock_times_out(self):
        target = Path(self.tmp.name) / "thing.json"
        with file_lock(target):
            with self.assertRaises(RuntimeError):
                with file_lock(target, timeout=0.2):
                    pass
        with file_lock(target, timeout=0.2):
            pass

    def test_remember_session_from_stale_metas_keeps_both(self):
        meta = get_or_create_conversation(None, ".", "grok")
        cid = meta["id"]
        stale_a = json.loads(json.dumps(meta))
        stale_b = json.loads(json.dumps(meta))
        remember_session(stale_a, "claude", "sess-claude", model="sonnet")
        remember_session(stale_b, "codex", "sess-codex", model="gpt")
        fresh = load_conversation(cid)
        self.assertEqual(session_record(fresh, "claude")["id"], "sess-claude")
        self.assertEqual(session_record(fresh, "codex")["id"], "sess-codex")
        self.assertEqual(session_record(fresh, "claude")["model"], "sonnet")
        self.assertEqual(fresh["participants"], ["grok", "claude", "codex"])
        # the in-memory dict is refreshed with the merged state
        self.assertIn("claude", stale_b["sessions"])

    def test_add_participant_merges(self):
        meta = get_or_create_conversation(None, ".", "grok")
        remember_session(meta, "claude", "s1")
        updated = add_participant(meta["id"], "gemini")
        self.assertEqual(updated["participants"], ["grok", "claude", "gemini"])
        self.assertEqual(session_record(updated, "claude")["id"], "s1")
        add_participant(meta["id"], "gemini")
        self.assertEqual(load_conversation(meta["id"])["participants"].count("gemini"), 1)
        self.assertIsNone(add_participant("cnothere", "gemini"))

    def test_corrupt_jsonl_line_is_skipped(self):
        cid = get_or_create_conversation(None, ".", "grok")["id"]
        append_message(cid, {"text": "one"})
        path = conversation_dir(cid) / "messages.jsonl"
        with path.open("ab") as handle:
            handle.write(b"{not json\n\xff\xfe\n")
        append_message(cid, {"text": "two"})
        self.assertEqual([m["text"] for m in read_messages(cid)], ["one", "two"])

    def test_append_after_truncated_last_line(self):
        cid = get_or_create_conversation(None, ".", "grok")["id"]
        path = conversation_dir(cid) / "messages.jsonl"
        path.write_bytes(b'{"text": "cut')
        append_message(cid, {"text": "after"})
        self.assertEqual([m["text"] for m in read_messages(cid)], ["after"])

    def test_corrupt_meta_falls_back_to_default(self):
        cid = get_or_create_conversation(None, ".", "grok")["id"]
        (conversation_dir(cid) / "meta.json").write_text("{oops", encoding="utf-8")
        self.assertIsNone(load_conversation(cid))
        self.assertEqual(list_conversations(), [])
        again = get_or_create_conversation(cid, ".", "claude")
        self.assertEqual(again["participants"], ["claude"])

    def test_inbox_entries_have_id_and_ts_and_consume_keeps_later_entries(self):
        enqueue_inbox("gemini", {"text": "one"})
        rows = read_inbox("gemini", consume=True)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["id"])
        self.assertTrue(rows[0]["ts"])
        enqueue_inbox("gemini", {"text": "two"})
        self.assertEqual([r["text"] for r in read_inbox("gemini")], ["two"])

    def test_inbox_consume_loses_nothing_under_concurrent_appends(self):
        total = 150
        done = threading.Event()

        def producer():
            for i in range(total):
                enqueue_inbox("codex", {"text": str(i)})
            done.set()

        thread = threading.Thread(target=producer)
        thread.start()
        seen = []
        while not done.is_set():
            seen.extend(read_inbox("codex", consume=True))
        thread.join()
        seen.extend(read_inbox("codex", consume=True))
        self.assertEqual(sorted(int(r["text"]) for r in seen), list(range(total)))

    def test_list_conversations_oldest_first(self):
        for cid, created in (
            ("cb", "2026-01-02T00:00:00Z"),
            ("ca", "2026-01-03T00:00:00Z"),
            ("cc", "2026-01-01T00:00:00Z"),
        ):
            write_json(
                conversation_dir(cid) / "meta.json",
                {"id": cid, "created_at": created, "participants": [], "sessions": {}},
            )
        self.assertEqual([m["id"] for m in list_conversations()], ["cc", "cb", "ca"])

    def test_list_jobs_survives_missing_and_corrupt_meta(self):
        create_job({"agent": "codex", "status": "ok"})
        job_dir("jnometa")
        bad = job_dir("jbad")
        (bad / "meta.json").write_text("garbage", encoding="utf-8")
        rows = list_jobs()
        ids = {row["id"] for row in rows}
        self.assertIn("jbad", ids)
        self.assertNotIn("jnometa", ids)
        self.assertEqual({r["status"] for r in rows if r["id"] == "jbad"}, {"corrupt"})

    def test_running_jobs_requires_live_pid(self):
        create_job({"id": "jalive", "agent": "claude", "conversation": "c1", "status": "running", "pid": os.getpid()})
        create_job({"id": "jdead", "agent": "claude", "conversation": "c1", "status": "running", "pid": dead_pid()})
        create_job({"id": "jother", "agent": "codex", "conversation": "c1", "status": "running", "pid": os.getpid()})
        create_job({"id": "jdone", "agent": "claude", "conversation": "c1", "status": "ok", "pid": os.getpid()})
        self.assertEqual([j["id"] for j in running_jobs("c1", "claude")], ["jalive"])
        self.assertEqual(running_jobs("c1", "claude", exclude="jalive"), [])

    def test_reap_marks_dead_running_job_as_error(self):
        meta = get_or_create_conversation(None, ".", "grok")
        job = create_job(
            {"agent": "codex", "from": "grok", "conversation": meta["id"], "status": "running", "pid": dead_pid()}
        )
        self.assertTrue(job_is_stale(job))
        reaped = reap_job(job)
        self.assertEqual(reaped["status"], "error")
        self.assertEqual(load_job(job["id"])["error"], "processo do agente morreu")
        self.assertEqual(read_messages(meta["id"])[-1]["kind"], "error")

    def test_fresh_queued_job_is_not_stale_but_old_one_is(self):
        job = create_job({"agent": "codex", "status": "queued"})
        self.assertFalse(job_is_stale(job))
        old = update_job(job["id"], {"created_at": "2020-01-01T00:00:00Z"})
        self.assertTrue(job_is_stale(old))
        self.assertEqual(reap_job(old)["status"], "error")

    def test_update_job_only_if_status(self):
        job = create_job({"agent": "codex", "status": "ok"})
        self.assertIsNone(update_job(job["id"], {"status": "error"}, only_if_status=("running",)))
        self.assertEqual(load_job(job["id"])["status"], "ok")

    def test_reused_pid_is_not_the_running_job(self):
        create_job(
            {"id": "jreuse", "agent": "claude", "conversation": "c1", "status": "running",
             "started_at": STARTED, "pid": os.getpid()}
        )
        job = load_job("jreuse")
        with mock.patch("agents_cli.bus.process_creation_time", return_value=filetime(STARTED_EPOCH + 60)):
            self.assertTrue(job_is_stale(job))
            self.assertEqual(running_jobs("c1", "claude"), [])
        with mock.patch("agents_cli.bus.process_creation_time", return_value=filetime(STARTED_EPOCH + 3)):
            self.assertFalse(job_is_stale(job))
            self.assertEqual([j["id"] for j in running_jobs("c1", "claude")], ["jreuse"])
        with mock.patch("agents_cli.bus.process_creation_time", return_value=filetime(STARTED_EPOCH - 600)):
            self.assertFalse(job_is_stale(job))
        with mock.patch("agents_cli.bus.process_creation_time", return_value=None):
            self.assertFalse(job_is_stale(job))

    def test_pid_reuse_check_needs_started_at(self):
        create_job({"id": "jnostart", "agent": "claude", "conversation": "c1", "status": "running", "pid": os.getpid()})
        with mock.patch("agents_cli.bus.process_creation_time", return_value=filetime(STARTED_EPOCH + 60)) as created:
            self.assertFalse(job_is_stale(load_job("jnostart")))
        created.assert_not_called()

    def test_running_jobs_reads_only_its_conversation_newest_first(self):
        create_job({"id": "jmine", "agent": "claude", "conversation": "cmine0000001", "status": "running", "pid": os.getpid()})
        create_job({"id": "jtheirs", "agent": "claude", "conversation": "cother000001", "status": "running", "pid": os.getpid()})
        create_job({"id": "jlatest", "agent": "claude", "conversation": "cmine0000001", "status": "running", "pid": os.getpid()})
        with mock.patch("agents_cli.bus.load_job", wraps=load_job) as loader:
            rows = running_jobs("cmine0000001", "claude")
        self.assertEqual([j["id"] for j in rows], ["jlatest", "jmine"])
        self.assertEqual(sorted(c.args[0] for c in loader.call_args_list), ["jlatest", "jmine"])


class SessionAndLauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTS_HOME"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("AGENTS_HOME", None)
        self.tmp.cleanup()

    def test_forget_session_only_when_id_matches(self):
        meta = get_or_create_conversation("cforget00001", ".", "grok")
        remember_session(meta, "codex", "sess-1", model="m")
        remember_session(meta, "claude", "sess-c")
        self.assertFalse(forget_session("cforget00001", "codex", "other"))
        self.assertEqual(session_record(load_conversation("cforget00001"), "codex")["id"], "sess-1")
        self.assertTrue(forget_session("cforget00001", "codex", "sess-1"))
        disk = load_conversation("cforget00001")
        self.assertEqual(session_record(disk, "codex"), {})
        self.assertEqual(session_record(disk, "claude")["id"], "sess-c")
        self.assertFalse(forget_session("cforget00001", "codex", "sess-1"))

    def test_queued_job_with_dead_launcher_is_stale_immediately(self):
        job = create_job({"agent": "codex", "status": "queued", "launcher_pid": dead_pid()})
        self.assertTrue(job_is_stale(job))
        self.assertEqual(reap_job(job)["status"], "error")

    def test_queued_job_with_live_launcher_is_not_stale_even_when_old(self):
        job = create_job(
            {"agent": "codex", "status": "queued", "launcher_pid": os.getpid(), "created_at": "2020-01-01T00:00:00Z"}
        )
        self.assertFalse(job_is_stale(job))
