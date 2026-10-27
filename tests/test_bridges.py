"""The Codex bridge, run against a fake codex that behaves like `codex exec --json`."""

from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from quantarium import agents
from quantarium.agents import AgentSettings
from quantarium.runtimes.codex_exec import DISABLED_FEATURES, CodexBridge
from quantarium.discovery import announce
from quantarium.server import QuantariumServer
from quantarium.workspace import Workspace


ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)
FAKE = Path(__file__).with_name("fake_codex.py")


class CodexBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.root = Path(self.temp.name)
        self.server = QuantariumServer(Workspace(self.root), port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        announce(self.root, self.server.url)
        self.api = self.server.api
        AgentSettings(self.api.workspace).enable(agents.CONFIRMATION, agents.NOTICE_VERSION)
        self.api.command("cell-create", {"name": "Existing"})
        self.connection = self.api.command("agent-connection-create", {
            "name": "ChatGPT via Codex", "kind": "bridge", "provider": {"bridge": "codex"}})["connection"]
        self.record = self.root / "record.jsonl"
        real = CodexBridge.command
        # Run the fake in place of codex, with the same arguments Quantarium builds.
        self.patch = mock.patch.object(CodexBridge, "command", lambda bridge, api, token, thread:
                                       [sys.executable, str(FAKE)] + real(bridge, api, token, thread)[1:])
        self.which = mock.patch("quantarium.runtimes.shutil.which", return_value="codex")

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def turn(self, chat_id: str, text: str) -> dict:
        self.api.command("chat-send", {"id": chat_id, "text": text})
        deadline = time.monotonic() + 60
        while self.api.assistant.busy(chat_id) and time.monotonic() < deadline:
            time.sleep(0.05)
        return self.api.get(f"agents/chats/{chat_id}", {})

    def test_two_turns_through_codex(self) -> None:
        with self.patch, self.which, mock.patch.dict(os.environ, {"FAKE_CODEX_RECORD": str(self.record),
                                                                  "ANTHROPIC_API_KEY": "sk-ant-secret"}):
            chat = self.api.command("chat-create", {"connection_id": self.connection["id"]})
            first = self.turn(chat["id"], "What cells do I have?")
            second = self.turn(chat["id"], "And now?")
        self.assertEqual(first["status"], "idle", first.get("error"))
        texts = [block["text"] for message in second["messages"] for block in message["content"]
                 if block["type"] == "text" and message["role"] == "assistant"]
        self.assertEqual(texts, ["You have 1 cells.", "Again!"])
        tools = [block["name"] for message in second["messages"] for block in message["content"] if block["type"] == "tool_use"]
        self.assertEqual(tools, ["list_cells", "list_cells"])
        self.assertEqual(second["usage"], {"input_tokens": 600, "output_tokens": 40})
        self.assertEqual(second["bridge"]["thread_id"], "11111111-2222-3333-4444-555555555555")

        runs = [json.loads(line) for line in self.record.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([run["status"] for run in runs], [200, 200])  # the turn's token worked
        args = runs[0]["args"]
        self.assertEqual(args[:2], ["exec", "--json"])
        for flag in ("--ignore-user-config", "--ignore-rules", "--skip-git-repo-check"):
            self.assertIn(flag, args)
        for feature in DISABLED_FEATURES:
            self.assertIn(feature, args)
        self.assertIn('sandbox_mode="read-only"', args)
        # Only Quantarium's own tools are pre-approved, and Codex's tool host stays on (it runs every tool call).
        self.assertIn('mcp_servers.quantarium.default_tools_approval_mode="approve"', args)
        self.assertNotIn("code_mode_host", args)
        self.assertEqual(runs[1]["args"][:3], ["exec", "resume", "11111111-2222-3333-4444-555555555555"])
        self.assertIn("You are the assistant built into Quantarium", runs[0]["prompt"])
        self.assertEqual(runs[1]["prompt"], "And now?")
        self.assertEqual(runs[0]["env"], [])  # no keys or tokens of the user's leak into Codex
        self.assertNotEqual(Path(runs[0]["cwd"]).resolve(), self.root.resolve())
        self.assertNotEqual(runs[0]["token"], runs[1]["token"])

        # Each turn's token stops working when the turn ends.
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=30)
        connection.request("GET", "/api/v1/cells", headers={"Authorization": f"Bearer {runs[1]['token']}"})
        response = connection.getresponse()
        self.assertEqual((response.status, json.loads(response.read())["code"]), (401, "unknown_token"))
        connection.close()
        log = {(entry["connection_name"], entry["name"], entry["outcome"]) for entry in self.api.get("agents/log", {})}
        self.assertIn(("ChatGPT via Codex", "cells", "ok"), log)

    def test_a_command_stops_the_turn(self) -> None:
        with self.patch, self.which, mock.patch.dict(os.environ, {"FAKE_CODEX_RECORD": str(self.record)}):
            chat = self.api.command("chat-create", {"connection_id": self.connection["id"]})
            started = time.monotonic()
            done = self.turn(chat["id"], "Please run a command")
        self.assertLess(time.monotonic() - started, 20)
        texts = [block["text"] for message in done["messages"] for block in message["content"] if block["type"] == "text"]
        self.assertTrue(any(text.startswith("Quantarium stopped this turn: Codex started a command") for text in texts), texts)
        self.assertNotIn("leaked", texts)
        self.assertIn('shell_environment_policy.inherit="none"', json.loads(self.record.read_text(encoding="utf-8"))["args"])

    def test_deleting_a_chat_removes_its_folder_and_optionally_codex_transcript(self) -> None:
        codex_home = self.root / "codex-home"
        thread = "11111111-2222-3333-4444-555555555555"
        with self.patch, self.which, mock.patch.dict(os.environ, {"FAKE_CODEX_RECORD": str(self.record),
                                                                  "CODEX_HOME": str(codex_home)}):
            transcript = codex_home / "sessions" / "2026" / "10" / "21" / f"rollout-2026-10-21T20-40-03-{thread}.jsonl"
            other = transcript.with_name("rollout-2026-10-21T20-40-03-99999999-2222-3333-4444-555555555555.jsonl")
            transcript.parent.mkdir(parents=True)
            transcript.write_text("{}", encoding="utf-8")
            other.write_text("{}", encoding="utf-8")
            first = self.api.command("chat-create", {"connection_id": self.connection["id"]})
            done = self.turn(first["id"], "What cells do I have?")
            self.assertEqual(done["app_transcripts"], [str(transcript)])
            folder = Path(done["bridge"]["folder"])
            self.assertTrue(folder.is_dir())
            result = self.api.command("chat-delete", {"id": first["id"], "codex_transcript": True})
            self.assertEqual((result["folder_removed"], result["app_transcripts_removed"]), (True, 1))
            self.assertFalse(folder.exists())
            self.assertFalse(transcript.exists())
            self.assertTrue(other.exists())  # only this chat's thread is touched
            # Without the option, Codex's transcript is left alone.
            transcript.write_text("{}", encoding="utf-8")
            second = self.api.command("chat-create", {"connection_id": self.connection["id"]})
            self.turn(second["id"], "Hi")
            result = self.api.command("chat-delete", {"id": second["id"]})
            self.assertEqual(result["app_transcripts_removed"], 0)
            self.assertTrue(transcript.exists())

    def test_cleanup_never_leaves_its_own_folders(self) -> None:
        from quantarium.runtimes import remove_turn_folder
        from quantarium.runtimes.codex_exec import codex_transcripts

        outside = self.root / "quantarium-codex-not-temp"
        outside.mkdir()
        self.assertFalse(remove_turn_folder({"folder": str(outside)}))
        self.assertTrue(outside.exists())
        self.assertFalse(remove_turn_folder({"folder": tempfile.gettempdir()}))
        self.assertEqual(codex_transcripts("../../*"), [])
        self.assertEqual(codex_transcripts("*"), [])

    def test_missing_codex_is_explained(self) -> None:
        with mock.patch("quantarium.runtimes.shutil.which", return_value=None):
            chat = self.api.command("chat-create", {"connection_id": self.connection["id"]})
            done = self.turn(chat["id"], "hi")
        self.assertEqual(done["status"], "error")
        self.assertIn("Codex is not installed", done["error"])
        self.assertIsNone(next((item for item in self.api.agents._listed(self.api.agents._read())
                                if item.get("session_token_sha256")), None))


if __name__ == "__main__":
    unittest.main()
