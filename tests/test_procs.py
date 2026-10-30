import os
import subprocess
import sys
import time
import unittest
from unittest import mock

from agents_cli import procs


class ProcsTests(unittest.TestCase):
    def test_pid_alive_sanity(self):
        self.assertTrue(procs.pid_alive(os.getpid()))
        self.assertFalse(procs.pid_alive(None))
        self.assertFalse(procs.pid_alive(0))
        self.assertFalse(procs.pid_alive(-5))

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_process_table_has_current_process(self):
        table = procs.process_table()
        self.assertIn(os.getpid(), table)
        ppid, name = table[os.getpid()]
        self.assertEqual(ppid, os.getppid())
        self.assertTrue(name.lower().endswith(".exe"))

    def test_ancestor_names_returns_list(self):
        names = procs.ancestor_names()
        self.assertIsInstance(names, list)
        self.assertLessEqual(len(names), 12)
        self.assertTrue(all(isinstance(n, str) for n in names))

    def test_ancestor_names_walks_up(self):
        table = {10: (5, "me.exe"), 5: (3, "bash.exe"), 3: (1, "codex.exe"), 1: (0, "init")}
        with mock.patch.object(procs, "process_table", return_value=table):
            with mock.patch.object(procs, "process_creation_time", return_value=None):
                self.assertEqual(procs.ancestor_names(10), ["bash.exe", "codex.exe", "init"])
                self.assertEqual(procs.ancestor_names(10, limit=2), ["bash.exe", "codex.exe"])

    def test_ancestor_names_stops_when_parent_pid_was_reused(self):
        table = {10: (5, "me.exe"), 5: (3, "bash.exe"), 3: (1, "reused.exe"), 1: (0, "init")}
        times = {10: 1000, 5: 900, 3: 5000, 1: 10}  # pid 3 started after its "child" 5
        with mock.patch.object(procs, "process_table", return_value=table):
            with mock.patch.object(procs, "process_creation_time", side_effect=lambda pid: times.get(pid)):
                self.assertEqual(procs.ancestor_names(10), ["bash.exe"])

    def test_ancestor_names_keeps_walking_when_times_are_unknown(self):
        table = {10: (5, "me.exe"), 5: (3, "bash.exe"), 3: (1, "codex.exe"), 1: (0, "init")}
        times = {10: 1000, 3: 500}  # pid 5 unreadable; 3 is older than 10
        with mock.patch.object(procs, "process_table", return_value=table):
            with mock.patch.object(procs, "process_creation_time", side_effect=lambda pid: times.get(pid)):
                self.assertEqual(procs.ancestor_names(10), ["bash.exe", "codex.exe", "init"])

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_process_creation_time_of_self(self):
        self.assertIsInstance(procs.process_creation_time(os.getpid()), int)
        self.assertIsNone(procs.process_creation_time(0))

    def test_kill_tree_taskkill_has_no_window(self):
        with mock.patch.object(procs.os, "name", "nt"):
            with mock.patch.object(procs.subprocess, "run") as run:
                procs.kill_tree(4242)
        self.assertEqual(run.call_args.args[0], ["taskkill", "/T", "/F", "/PID", "4242"])
        self.assertEqual(run.call_args.kwargs["creationflags"], 0x08000000)

    def test_ancestor_names_survives_cycles(self):
        table = {10: (5, "a"), 5: (6, "b"), 6: (5, "c")}
        with mock.patch.object(procs, "process_table", return_value=table):
            with mock.patch.object(procs, "process_creation_time", return_value=None):
                self.assertEqual(procs.ancestor_names(10), ["b", "c"])

    def test_ancestor_names_empty_table(self):
        with mock.patch.object(procs, "process_table", return_value={}):
            self.assertIsInstance(procs.ancestor_names(), list)

    def test_kill_tree_kills_child(self):
        kwargs = {"start_new_session": True} if os.name != "nt" else {}
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        try:
            self.assertTrue(procs.pid_alive(child.pid))
            procs.kill_tree(child.pid)
            child.wait(timeout=10)
            self.assertIsNotNone(child.returncode)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()

    def test_kill_tree_ignores_bad_pid(self):
        procs.kill_tree(0)
        procs.kill_tree(-1)

    def test_kill_tree_kills_grandchild(self):
        code = (
            "import subprocess, sys, time; "
            "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
            "print(g.pid, flush=True); time.sleep(30)"
        )
        kwargs = {"start_new_session": True} if os.name != "nt" else {}
        child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, **kwargs)
        try:
            grand = int(child.stdout.readline().strip())
            self.assertTrue(procs.pid_alive(grand))
            procs.kill_tree(child.pid)
            child.wait(timeout=10)
            deadline = time.monotonic() + 10
            while procs.pid_alive(grand) and time.monotonic() < deadline:
                time.sleep(0.1)
            self.assertFalse(procs.pid_alive(grand))
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            child.stdout.close()


if __name__ == "__main__":
    unittest.main()
