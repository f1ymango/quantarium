"""The security harness checks itself: it must notice the leaks and changes it exists to catch."""

from __future__ import annotations

import os
import socket
import tempfile
import unittest
from pathlib import Path

from .harness import TEST_TEMP, Canaries, FolderGuard, Fuzzer, MisuseCase, NetworkGuard


class HarnessSelfTests(unittest.TestCase):
    def test_canaries_are_found_wherever_they_leak(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            base = Path(temp)
            canaries = Canaries(base)
            with canaries.active():
                key = os.environ["ANTHROPIC_API_KEY"]
                self.assertTrue(key.startswith("BLCANARY-"))
                self.assertEqual(Path.home() if os.name != "nt" else Path(os.environ["USERPROFILE"]), canaries.home)
            self.assertNotIn(key, os.environ.get("ANTHROPIC_API_KEY", ""))
            self.assertEqual(canaries.found_in_text({"error": f"bad key {key}"}), ["env:ANTHROPIC_API_KEY"])
            leaked = base / "workspace" / "runs" / "journal.sqlite"
            leaked.parent.mkdir(parents=True)
            leaked.write_bytes(b"\x00binary" + canaries.values["file:.ssh/id_ed25519"].encode() + b"\x00")
            self.assertEqual(canaries.found_in_folder(base / "workspace"), ["file:.ssh/id_ed25519 in journal.sqlite"])
            self.assertEqual(canaries.found_in_text("nothing here"), [])

    def test_the_folder_guard_notices_additions_changes_and_removals(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            folder = Path(temp)
            (folder / "kept.txt").write_text("a", encoding="utf-8")
            (folder / "gone.txt").write_text("b", encoding="utf-8")
            guard = FolderGuard(folder)
            self.assertEqual(guard.changes(), [])
            (folder / "kept.txt").write_text("changed", encoding="utf-8")
            (folder / "gone.txt").unlink()
            (folder / "new.txt").write_text("c", encoding="utf-8")
            kinds = sorted(change.split(" ", 1)[0] for change in guard.changes())
            self.assertEqual(kinds, ["added", "changed", "removed"])

    def test_the_fuzzer_replays_from_its_seed(self) -> None:
        outside = Path("outside.txt")
        first = [Fuzzer(7, outside).params(["id", "key", "spec"]) for _ in range(1)]
        again = [Fuzzer(7, outside).params(["id", "key", "spec"]) for _ in range(1)]
        other = [Fuzzer(8, outside).params(["id", "key", "spec"]) for _ in range(1)]
        self.assertEqual(first, again)
        self.assertNotEqual(first, other)


    def test_the_network_guard_refuses_everything_but_loopback(self) -> None:
        with NetworkGuard() as guard:
            with self.assertRaises(OSError):
                socket.getaddrinfo("example.com", 443)
            with socket.socket() as sock, self.assertRaises(OSError):
                sock.connect(("192.0.2.1", 443))
            socket.getaddrinfo("127.0.0.1", 80)
        self.assertEqual(guard.attempts, ["example.com", "192.0.2.1"])


class MisuseCaseSelfTest(MisuseCase):
    def test_a_hostile_agent_is_connected_and_checked(self) -> None:
        status, cells = self.agent("get", "cells")
        self.assertEqual((status, cells), (200, []))
        status, body = self.agent("get", "agent-log")
        self.assertEqual(status, 200)

    def test_keys_go_to_a_fake_keychain(self) -> None:
        import keyring

        keyring.set_password("quantarium", "test", "value")
        self.assertEqual(self.keyring.store, {("quantarium", "test"): "value"})


if __name__ == "__main__":
    unittest.main()
