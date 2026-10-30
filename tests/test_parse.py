import os
import tempfile
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

    def test_free_text_task_keeps_first_word(self):
        req = parse_tokens(["codex", "fix", "the", "bug"])
        self.assertIsNone(req.model)
        self.assertIsNone(req.effort)
        self.assertEqual(req.prompt, "fix the bug")

    def test_known_model_name_and_effort(self):
        req = parse_tokens(["claude", "sonnet", "high", "fix", "it"])
        self.assertEqual((req.model, req.effort, req.prompt), ("sonnet", "high", "fix it"))

    def test_model_shaped_tokens(self):
        self.assertEqual(parse_tokens(["codex", "gpt-6.1-sol", "do", "it"]).model, "gpt-6.1-sol")
        self.assertEqual(parse_tokens(["grok", "x/y:z", "hi"]).model, "x/y:z")
        self.assertEqual(parse_tokens(["codex", "o3", "hi"]).model, "o3")

    def test_prose_with_whitespace_or_punctuation_is_not_model(self):
        self.assertIsNone(parse_tokens(["codex", "fix the bug"]).model)
        self.assertIsNone(parse_tokens(["codex", "Fix!", "now"]).model)

    def test_configured_model_is_recognised(self):
        self.assertIsNone(parse_tokens(["codex", "turbo", "go"]).model)
        req = parse_tokens(["codex", "turbo", "go"], known_models=["turbo"])
        self.assertEqual((req.model, req.prompt), ("turbo", "go"))

    def test_file_like_first_word_is_not_a_model(self):
        req = parse_tokens(["codex", "README.md", "has", "a", "typo"])
        self.assertIsNone(req.model)
        self.assertEqual(req.prompt, "README.md has a typo")
        for name in ("main.py", "app.TSX", "build.cmd", "notes.txt", "data.csv", "x.lock"):
            self.assertIsNone(parse_tokens(["codex", name, "now"]).model, name)
        req = parse_tokens(["codex", "gpt-6.1-sol", "fix"])
        self.assertEqual((req.model, req.prompt), ("gpt-6.1-sol", "fix"))

    def test_existing_path_is_not_a_model(self):
        old = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                self.assertEqual(parse_tokens(["codex", "foo-1", "go"]).model, "foo-1")
                open("foo-1", "w").close()
                self.assertIsNone(parse_tokens(["codex", "foo-1", "go"]).model)
                os.mkdir("sub-2")
                self.assertIsNone(parse_tokens(["codex", "sub-2", "go"]).model)
            finally:
                os.chdir(old)

    def test_prompt_given_ignores_file_heuristic(self):
        req = parse_tokens(["codex", "README.md"], prompt_given=True)
        self.assertEqual(req.model, "README.md")

    def test_prompt_given_keeps_first_token_as_model(self):
        req = parse_tokens(["codex", "whatever", "high"], prompt_given=True)
        self.assertEqual((req.model, req.effort, req.prompt), ("whatever", "high", ""))
        req = parse_tokens(["codex", "high"], prompt_given=True)
        self.assertEqual((req.model, req.effort), (None, "high"))

    def test_hyphenated_word_is_prose_not_model(self):
        req = parse_tokens(["claude", "corrija-o", "agora"])
        self.assertEqual((req.model, req.prompt), (None, "corrija-o agora"))
        req = parse_slash("/delegate claude corrija-o agora")
        self.assertEqual((req.model, req.prompt), (None, "corrija-o agora"))

    def test_bare_number_is_not_a_model(self):
        req = parse_tokens(["codex", "3", "testes", "falhando"])
        self.assertEqual((req.model, req.prompt), (None, "3 testes falhando"))

    def test_names_with_digits_dots_or_known_names_are_models(self):
        for name in ("gpt-6.1-sol", "grok-4.6", "gemini-3.8-flash", "sonnet", "haiku-5.5"):
            req = parse_tokens(["codex", name, "go"])
            self.assertEqual((req.model, req.prompt), (name, "go"), name)

    def test_file_lookup_uses_given_cwd(self):
        with tempfile.TemporaryDirectory() as here, tempfile.TemporaryDirectory() as other:
            open(os.path.join(here, "foo-1"), "w").close()
            self.assertIsNone(parse_tokens(["codex", "foo-1", "go"], cwd=here).model)
            self.assertEqual(parse_tokens(["codex", "foo-1", "go"], cwd=other).model, "foo-1")
