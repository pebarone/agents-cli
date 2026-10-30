import os
import tempfile
import unittest
from pathlib import Path

from agents_cli.bus import (
    append_message,
    create_job,
    enqueue_inbox,
    get_or_create_conversation,
    read_inbox,
    read_messages,
    remember_session,
    session_record,
)


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
