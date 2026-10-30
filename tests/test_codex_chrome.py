import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agents_cli import codex_chrome


def _by_name(rows):
    return {row["name"]: row for row in rows}


class CodexChromeTests(unittest.TestCase):
    def test_non_windows(self):
        with mock.patch.object(codex_chrome.os, "name", "posix"):
            rows = codex_chrome.diagnose()
        by = _by_name(rows)
        self.assertEqual(by["codex-chrome"]["status"], "info")
        self.assertIn("Windows", by["codex-chrome"]["detail"])
        self.assertIn("sessoes do codex", by)

    def _patched(self, pipes, tmp, procs_table=None):
        manifest = Path(tmp) / "manifest.json"
        exe = Path(tmp) / "host.exe"
        exe.write_text("x")
        manifest.write_text(
            json.dumps({"path": str(exe), "allowed_origins": [codex_chrome.EXTENSION_ORIGIN]}),
            encoding="utf-8",
        )
        stack = [
            mock.patch.object(codex_chrome.os, "name", "nt"),
            mock.patch.object(codex_chrome, "_manifest_path", return_value=str(manifest)),
            mock.patch.object(codex_chrome.os, "listdir", return_value=pipes),
            mock.patch.object(codex_chrome.Path, "home", return_value=Path(tmp)),
            mock.patch.object(
                codex_chrome.procs,
                "process_table",
                return_value=procs_table if procs_table is not None else {},
            ),
        ]
        return stack

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_fail_when_no_pipe(self):
        with tempfile.TemporaryDirectory() as tmp:
            stack = self._patched(["other-pipe"], tmp)
            for p in stack:
                p.start()
            try:
                rows = _by_name(codex_chrome.diagnose())
            finally:
                for p in stack:
                    p.stop()
        self.assertEqual(rows["ponte do app (pipe)"]["status"], "fail")
        self.assertIn("app desktop do Codex", rows["ponte do app (pipe)"]["detail"])
        self.assertEqual(rows["native host"]["status"], "ok")
        self.assertEqual(rows["extension-host"]["status"], "warn")
        self.assertEqual(rows["nota"]["status"], "info")

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_ok_when_pipe_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            stack = self._patched(["codex-browser-use-abc", "x"], tmp, {7: (1, "extension-host.exe")})
            for p in stack:
                p.start()
            try:
                rows = _by_name(codex_chrome.diagnose())
            finally:
                for p in stack:
                    p.stop()
        self.assertEqual(rows["ponte do app (pipe)"]["status"], "ok")
        self.assertEqual(rows["extension-host"]["status"], "ok")

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_registry_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_dir = Path(tmp) / ".codex"
            codex_dir.mkdir()
            existing = Path(tmp) / "exists.txt"
            existing.write_text("x")
            entries = [
                {"presence": {"pid": 111}, "paths": {"a": str(existing), "dirs": [str(existing)]}},
                {"presence": {"pid": 222}, "paths": {"a": str(Path(tmp) / "missing")}},
            ]
            (codex_dir / "chrome-native-hosts-v2.json").write_text(json.dumps({"entries": entries}), encoding="utf-8")
            stack = self._patched([], tmp)
            for p in stack:
                p.start()
            try:
                with mock.patch.object(codex_chrome.procs, "pid_alive", side_effect=lambda pid: pid == 111):
                    rows = _by_name(codex_chrome.diagnose())
            finally:
                for p in stack:
                    p.stop()
        row = rows["registro de runtimes"]
        self.assertEqual(row["status"], "ok")
        self.assertIn("2 entrada", row["detail"])
        self.assertIn("1 vivas e completas", row["detail"])
        self.assertNotIn(tmp, row["detail"])

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_never_raises_when_everything_throws(self):
        boom = RuntimeError("boom")
        with mock.patch.object(codex_chrome.os, "name", "nt"):
            with mock.patch.object(codex_chrome, "_manifest_path", side_effect=boom):
                with mock.patch.object(codex_chrome.os, "listdir", side_effect=boom):
                    with mock.patch.object(codex_chrome.procs, "process_table", side_effect=boom):
                        with mock.patch.object(codex_chrome.Path, "home", side_effect=boom):
                            rows = codex_chrome.diagnose()
        by = _by_name(rows)
        for name in ("native host", "extension-host", "ponte do app (pipe)", "processos codex", "registro de runtimes"):
            self.assertEqual(by[name]["status"], "warn", name)


class SessionStorageTests(unittest.TestCase):
    def _row(self, tmp):
        with mock.patch.object(codex_chrome.Path, "home", return_value=Path(tmp)):
            return _by_name(codex_chrome._check_sessions())["sessoes do codex"]

    def test_missing_is_info(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = self._row(tmp)
        self.assertEqual(row["status"], "info")

    def test_plain_directories_are_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".codex" / "sessions").mkdir(parents=True)
            (Path(tmp) / ".codex" / "archived_sessions").mkdir()
            row = self._row(tmp)
        self.assertEqual(row["status"], "ok")

    def test_one_directory_is_enough_for_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".codex" / "sessions").mkdir(parents=True)
            row = self._row(tmp)
        self.assertEqual(row["status"], "ok")

    def test_dangling_link_fails_with_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex = Path(tmp) / ".codex"
            codex.mkdir()
            link = codex / "sessions"
            target = Path(tmp) / "unmounted"
            if os.name == "nt":
                import subprocess

                target.mkdir()
                made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
                if made.returncode != 0:
                    self.skipTest("nao consegui criar junction")
                target.rmdir()  # the junction now dangles
            else:
                try:
                    os.symlink(target, link)
                except (OSError, NotImplementedError):
                    self.skipTest("sem symlink")
            row = self._row(tmp)
            if os.name == "nt":
                os.rmdir(link)
        self.assertEqual(row["status"], "fail")
        self.assertIn("sessions aponta para", row["detail"])
        self.assertIn("unmounted", row["detail"])
        self.assertIn("no rollout found", row["detail"])
        self.assertIn("Monte o drive ou aponte para uma pasta local", row["detail"])

    def test_link_target_strips_extended_prefixes(self):
        cases = {
            "\\\\?\\UNC\\server\\share\\codex": "\\\\server\\share\\codex",
            "\\\\?\\D:\\dados\\sessions": "D:\\dados\\sessions",
            "C:\\local\\sessions": "C:\\local\\sessions",
        }
        for raw, expected in cases.items():
            with mock.patch.object(codex_chrome.os.path, "islink", return_value=True), mock.patch.object(
                codex_chrome.os, "readlink", return_value=raw
            ):
                self.assertEqual(codex_chrome._link_target(Path("sessions")), expected, raw)

    def test_never_raises(self):
        with mock.patch.object(codex_chrome.Path, "home", side_effect=RuntimeError("boom")):
            rows = codex_chrome.diagnose()
        self.assertEqual(_by_name(rows)["sessoes do codex"]["status"], "warn")


if __name__ == "__main__":
    unittest.main()
