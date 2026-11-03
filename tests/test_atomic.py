"""Saving a file while another thread reads it (quantarium.atomic).

On Windows, replacing a file that is open fails, and opening a file that is
being replaced fails; both raise PermissionError. Before the retry, a reader
that met a save saw agent settings as empty, so agent features looked off and
a bridge connection looked unknown, and a save could fail outright.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from quantarium import agents, atomic
from quantarium.agents import AgentSettings
from quantarium.assistant import Chats
from quantarium.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)


def clashes(times: int, then=None):
    """A stand-in that raises PermissionError ``times`` times, then calls ``then``."""
    calls = []

    def action(*args, **options):
        calls.append(args)
        if len(calls) <= times:
            raise PermissionError(13, "The process cannot access the file because it is being used")
        return then(*args, **options) if then else None
    return action, calls


class RetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_a_replace_that_meets_a_reader_is_retried(self) -> None:
        source, target = self.root / "a.tmp", self.root / "a.json"
        source.write_text("new", encoding="utf-8")
        target.write_text("old", encoding="utf-8")
        replace, calls = clashes(3, os.replace)
        with mock.patch.object(atomic, "RETRY", True), mock.patch.object(atomic.os, "replace", replace):
            atomic.replace(source, target)
        self.assertEqual(len(calls), 4)
        self.assertEqual(target.read_text(encoding="utf-8"), "new")

    def test_a_read_that_meets_a_replace_is_retried(self) -> None:
        path = self.root / "a.json"
        path.write_text("{}", encoding="utf-8")
        real = Path.read_text
        read, calls = clashes(2, lambda self, **options: real(self, **options))
        with mock.patch.object(atomic, "RETRY", True), mock.patch.object(Path, "read_text", read):
            self.assertEqual(atomic.read_text(path), "{}")
        self.assertEqual(len(calls), 3)

    def test_a_lasting_clash_is_raised(self) -> None:
        replace, calls = clashes(10 ** 6)
        with mock.patch.object(atomic, "RETRY", True), mock.patch.object(atomic, "RETRY_SECONDS", 0.05), \
                mock.patch.object(atomic.os, "replace", replace):
            with self.assertRaises(PermissionError):
                atomic.replace(self.root / "a.tmp", self.root / "a.json")
        self.assertGreater(len(calls), 1)

    def test_outside_windows_a_permission_error_is_raised_at_once(self) -> None:
        replace, calls = clashes(1)
        with mock.patch.object(atomic, "RETRY", False), mock.patch.object(atomic.os, "replace", replace):
            with self.assertRaises(PermissionError):
                atomic.replace(self.root / "a.tmp", self.root / "a.json")
        self.assertEqual(len(calls), 1)


class ConcurrentSaveTests(unittest.TestCase):
    """Real threads on real files: fails on Windows without the retry."""

    SECONDS = 1.5

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.workspace = Workspace(Path(self.temp.name))
        self.addCleanup(self.temp.cleanup)

    def hammer(self, write, read) -> tuple[list, list]:
        """Write in one thread and read in another; return what went wrong on each side."""
        failed_writes, bad_reads = [], []
        deadline = time.monotonic() + self.SECONDS

        def writer() -> None:
            while time.monotonic() < deadline:
                try:
                    write()
                except Exception as exc:
                    failed_writes.append(repr(exc))

        thread = threading.Thread(target=writer)
        thread.start()
        while time.monotonic() < deadline:
            try:
                problem = read()
            except Exception as exc:
                problem = repr(exc)
            if problem:
                bad_reads.append(problem)
        thread.join()
        return failed_writes, bad_reads

    def test_agent_settings_stay_on_while_another_thread_saves_them(self) -> None:
        settings = AgentSettings(self.workspace)
        settings.enable(agents.CONFIRMATION, agents.NOTICE_VERSION)
        reader = AgentSettings(self.workspace)
        failed_writes, bad_reads = self.hammer(
            lambda: settings.enable(agents.CONFIRMATION, agents.NOTICE_VERSION),
            lambda: None if reader.enabled() else "agent features read as off")
        self.assertEqual(failed_writes, [])
        self.assertEqual(bad_reads[:3], [])

    def test_a_chat_stays_readable_while_its_turn_saves_it(self) -> None:
        chats = Chats(self.workspace)
        chat = chats.create({"id": "c_1", "name": "Assistant"})
        failed_writes, bad_reads = self.hammer(
            lambda: chats.save(chat),
            lambda: None if chats.get(chat["id"])["id"] == chat["id"] else "wrong chat")
        self.assertEqual(failed_writes[:3], [])
        self.assertEqual(bad_reads[:3], [])


if __name__ == "__main__":
    unittest.main()
