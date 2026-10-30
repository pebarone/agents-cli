import unittest

from agents_cli.parse import ParseError, normalize_effort, parse_slash, parse_tokens


class ParseTests(unittest.TestCase):
    def test_full_slash(self):
        req = parse_slash("/delegate grok grok-4.6 high review the diff")
        self.assertEqual(req.agent, "grok")
        self.assertEqual(req.model, "grok-4.6")
        self.assertEqual(req.effort, "high")
        self.assertEqual(req.prompt, "review the diff")

    def test_effort_without_model(self):
        req = parse_slash("/delegate claude xhigh implement the test")
        self.assertEqual(req.agent, "claude")
        self.assertIsNone(req.model)
        self.assertEqual(req.effort, "xhigh")
        self.assertEqual(req.prompt, "implement the test")

    def test_aliases(self):
        self.assertEqual(parse_tokens(["agy", "high", "hi"]).agent, "gemini")
        self.assertEqual(parse_tokens(["chatgpt"]).agent, "codex")
        self.assertEqual(parse_tokens(["antigravity"]).agent, "gemini")

    def test_unknown_agent(self):
        with self.assertRaises(ParseError):
            parse_tokens(["bard"])

    def test_unknown_effort(self):
        with self.assertRaises(ParseError):
            normalize_effort("insane")

    def test_model_then_prompt(self):
        req = parse_slash("/delegate grok grok-4.6 review the diff")
        self.assertEqual(req.model, "grok-4.6")
        self.assertIsNone(req.effort)
        self.assertEqual(req.prompt, "review the diff")
