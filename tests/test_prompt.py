import unittest

from agents_cli.prompt import render_history, wrap_prompt


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
