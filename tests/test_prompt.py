import unittest

from agents_cli.prompt import TRUNCATED, render_history, wrap_prompt


class PromptTests(unittest.TestCase):
    def test_wrap_contains_protocol(self):
        text = wrap_prompt(
            body="review src/app.ts",
            from_agent="grok",
            to_agent="claude",
            model="sonnet",
            effort="high",
            conversation="c1",
            job="j1",
            cwd="C:/repo",
            depth=1,
            max_depth=4,
            resume=None,
        )
        self.assertIn("[agents-bus protocol=1]", text)
        self.assertIn("from: grok", text)
        self.assertIn("to: claude", text)
        self.assertIn("review src/app.ts", text)
        self.assertIn("agents.py", text)
        self.assertIn("python -u", text)
        self.assertIn("command -v agents", text)
        self.assertIn("This is a new session", text)

    def test_max_depth_forbids_nested(self):
        text = wrap_prompt(
            body="x",
            from_agent="a",
            to_agent="b",
            model="m",
            effort="high",
            conversation="c",
            job="j",
            cwd=".",
            depth=4,
            max_depth=4,
            resume="sess",
        )
        self.assertIn("Do not call `agents delegate`", text)
        self.assertIn("resume: sess", text)
        self.assertIn("follow-up in an existing harness session", text)

    def test_history(self):
        rendered = render_history(
            [
                {"from": "grok", "text": "please review"},
                {"from": "claude", "text": "looks fine"},
            ]
        )
        self.assertIn("[grok]", rendered)
        self.assertIn("looks fine", rendered)

    def test_envelope_says_final_answer_is_the_reply(self):
        text = wrap_prompt(
            body="x",
            from_agent="a",
            to_agent="b",
            model="m",
            effort="high",
            conversation="c1",
            job="j1",
            cwd=".",
            depth=1,
            max_depth=4,
            resume=None,
        )
        self.assertIn("delivered back to the sender", text)
        self.assertIn("Do NOT run `agents reply`", text)
        self.assertIn("--new", text)
        self.assertIn("--prompt-file", text)
        self.assertIn("--background", text)
        self.assertIn(" wait <job>", text)
        self.assertIn("conversation: c1", text)

    def test_history_caps_each_message(self):
        rendered = render_history([{"from": "a", "text": "x" * 10000}], max_message_chars=100)
        self.assertIn(TRUNCATED, rendered)
        self.assertLess(len(rendered), 400)

    def test_history_caps_total_keeping_most_recent(self):
        messages = [{"from": f"s{i}", "text": f"msg{i} " + "y" * 90} for i in range(30)]
        rendered = render_history(messages, limit=30, max_message_chars=4000, max_total_chars=1000)
        self.assertIn("msg29", rendered)
        self.assertNotIn("msg0 ", rendered)
        self.assertIn(TRUNCATED, rendered)
        self.assertLess(len(rendered), 1300)

    def test_history_without_truncation_has_no_marker(self):
        rendered = render_history([{"from": "a", "text": "short"}])
        self.assertNotIn(TRUNCATED, rendered)
