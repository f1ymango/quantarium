"""Agent runtimes: the generic ACP client and the Claude Code profile, run against fakes."""

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

from quantarium import agents, presets
from quantarium.agents import AgentSettings
from quantarium.discovery import announce
from quantarium.runtimes.claude_code import ClaudeCodeRuntime
from quantarium.server import QuantariumServer
from quantarium.workspace import Workspace


ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)
FAKE_ACP = Path(__file__).with_name("fake_acp.py")
FAKE_CLAUDE = Path(__file__).with_name("fake_claude.py")


class RuntimeCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.root = Path(self.temp.name)
        self.server = QuantariumServer(Workspace(self.root), port=0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        announce(self.root, self.server.url)
        self.api = self.server.api
        AgentSettings(self.api.workspace).enable(agents.CONFIRMATION, agents.NOTICE_VERSION)
        self.api.command("cell-create", {"name": "Existing"})
        self.record = self.root / "record.jsonl"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def turn(self, chat_id: str, text: str, timeout: float = 60) -> dict:
        self.api.command("chat-send", {"id": chat_id, "text": text})
        deadline = time.monotonic() + timeout
        while self.api.assistant.busy(chat_id) and time.monotonic() < deadline:
            time.sleep(0.05)
        return self.api.get(f"agents/chats/{chat_id}", {})

    def runs(self) -> list[dict]:
        return [json.loads(line) for line in self.record.read_text(encoding="utf-8").splitlines()]

    def bearer_status(self, token: str) -> int:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        connection.request("GET", "/api/v1/cells", headers={"Authorization": f"Bearer {token}"})
        status = connection.getresponse().status
        connection.close()
        return status

    @staticmethod
    def texts(chat: dict) -> list[str]:
        return [block["text"] for message in chat["messages"] for block in message["content"]
                if block["type"] == "text" and message["role"] == "assistant"]


class AcpTests(RuntimeCase):
    def connect(self) -> dict:
        return self.api.command("agent-connection-create", {
            "name": "Fake ACP", "kind": "bridge",
            "provider": {"runtime": "custom-acp", "command": [sys.executable, str(FAKE_ACP)]}})["connection"]

    def environment(self, mode: str = "normal"):
        return mock.patch.dict(os.environ, {"FAKE_ACP_RECORD": str(self.record), "FAKE_ACP_MODE": mode,
                                            "ANTHROPIC_API_KEY": "sk-ant-secret"})

    def test_a_turn_through_an_acp_agent(self) -> None:
        connection = self.connect()
        with self.environment():
            chat = self.api.command("chat-create", {"connection_id": connection["id"]})
            first = self.turn(chat["id"], "What cells do I have?")
            second = self.turn(chat["id"], "And now?")
        self.assertEqual(first["status"], "idle", first.get("error"))
        self.assertEqual(self.texts(first), ["Quantarium refused the agent's request to use Run rm -rf strategies.",
                                             "You have 1 cells."])
        tools = [block["name"] for message in first["messages"] for block in message["content"]
                 if block["type"] == "tool_use"]
        self.assertEqual(tools, ["list_cells"])
        self.assertEqual(first["usage"], {"input_tokens": 100, "output_tokens": 7})
        self.assertEqual(second["bridge"]["session_id"], "sess-1")
        self.assertNotIn("OLD HISTORY", " ".join(self.texts(second)))  # a loaded session's replay is not repeated

        runs = self.runs()
        init = runs[0]["initialize"]
        # Quantarium offers the agent no file or terminal access of its own.
        self.assertEqual(init["clientCapabilities"], {"fs": {"readTextFile": False, "writeTextFile": False},
                                                      "terminal": False})
        server = runs[1]["new"]["mcpServers"][0]
        self.assertEqual((server["name"], server["args"][:3]), ("quantarium", ["-m", "quantarium", "mcp"]))
        self.assertEqual([item["name"] for item in server["env"]], ["QUANTARIUM_TOKEN"])
        self.assertTrue(Path(runs[1]["new"]["cwd"]).name.startswith("quantarium-turn-"))
        prompts = [run["prompt"] for run in runs if "prompt" in run]
        self.assertIn("You are the assistant built into Quantarium", prompts[0])
        self.assertEqual(prompts[1], "And now?")  # a loaded session needs no instructions again
        self.assertIn({"load": "sess-1"}, runs)
        outcome = next(run for run in runs if "allow" in run)
        self.assertEqual(outcome["allow"], {"outcome": {"outcome": "selected", "optionId": "yes"}})
        self.assertEqual(outcome["shell"], {"outcome": {"outcome": "selected", "optionId": "no"}})
        self.assertEqual(outcome["read"]["code"], -32601)
        self.assertEqual((outcome["status"], outcome["env"]), (200, []))
        # The turn's token stops working when the turn ends.
        token = next(item["value"] for item in server["env"])
        self.assertEqual(self.bearer_status(token), 401)

    def test_an_agent_without_session_loading_gets_a_recap(self) -> None:
        connection = self.connect()
        with self.environment("noload"):
            chat = self.api.command("chat-create", {"connection_id": connection["id"]})
            self.turn(chat["id"], "What cells do I have?")
            self.turn(chat["id"], "And now?")
        prompts = [run["prompt"] for run in self.runs() if "prompt" in run]
        self.assertIn("The conversation so far:", prompts[1])
        self.assertIn("You have 1 cells.", prompts[1])
        self.assertEqual(sum(1 for run in self.runs() if "new" in run), 2)

    def test_an_unannounced_local_tool_stops_the_turn(self) -> None:
        connection = self.connect()
        started = time.monotonic()
        with self.environment("rogue"):
            chat = self.api.command("chat-create", {"connection_id": connection["id"]})
            done = self.turn(chat["id"], "Read the settings file")
        self.assertLess(time.monotonic() - started, 20)
        self.assertTrue(any(text.startswith("Quantarium stopped this turn: the agent used a tool outside Quantarium's")
                            for text in self.texts(done)), self.texts(done))

    def test_stopping_a_turn_cancels_the_session(self) -> None:
        connection = self.connect()
        with self.environment("slow"):
            chat = self.api.command("chat-create", {"connection_id": connection["id"]})
            self.api.command("chat-send", {"id": chat["id"], "text": "Take your time"})
            deadline = time.monotonic() + 20
            while not any("prompt" in run for run in (self.runs() if self.record.exists() else [])):
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.05)
            self.api.command("chat-stop", {"id": chat["id"]})
            while self.api.assistant.busy(chat["id"]):
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.05)
        self.assertIn({"cancel": True}, self.runs())

    def test_the_opencode_preset_turns_its_own_tools_off(self) -> None:
        preset = dict(presets.runtime_preset("opencode"))
        self.assertEqual(preset["command"], ["opencode", "acp"])
        connection = self.api.command("agent-connection-create", {
            "name": "OpenCode", "kind": "bridge", "provider": {"runtime": "opencode"}})["connection"]
        self.assertNotIn("command", connection)
        fake = {**preset, "command": [sys.executable, str(FAKE_ACP)]}
        with self.environment(), mock.patch.dict(os.environ, {"FAKE_ACP_TOOL_TITLE": "quantarium_list_cells"}),                 mock.patch("quantarium.presets.runtime_preset", lambda key: fake if key == "opencode" else None):
            chat = self.api.command("chat-create", {"connection_id": connection["id"]})
            done = self.turn(chat["id"], "What cells do I have?")
        self.assertEqual(done["status"], "idle", done.get("error"))
        self.assertIn("You have 1 cells.", self.texts(done))
        outcome = next(run for run in self.runs() if "allow" in run)
        self.assertEqual(outcome["allow"], {"outcome": {"outcome": "selected", "optionId": "yes"}})
        config = json.loads(outcome["opencode_config"])
        # Everything asks first, so Quantarium decides; OpenCode's own tools are off and denied.
        self.assertEqual(config["permission"]["*"], "ask")
        for tool in ("shell", "edit", "read", "webfetch", "external_directory"):
            self.assertEqual(config["permission"][tool], "deny")
        for tool in ("shell", "bash", "edit", "write", "read", "webfetch"):
            self.assertIs(config["tools"][tool], False)

    def test_a_missing_command_says_so(self) -> None:
        connection = self.api.command("agent-connection-create", {
            "name": "Missing", "kind": "bridge", "provider": {"runtime": "custom-acp", "command": ["no-such-agent-x"]}})
        chat = self.api.command("chat-create", {"connection_id": connection["connection"]["id"]})
        done = self.turn(chat["id"], "Hello")
        self.assertEqual(done["status"], "error")
        self.assertIn("no-such-agent-x is not installed", done["error"])


class ClaudeCodeTests(RuntimeCase):
    def setUp(self) -> None:
        super().setUp()
        self.ack = presets.runtime_preset("claude-code")["acknowledgement"]
        real = ClaudeCodeRuntime.command
        self.patch = mock.patch.object(ClaudeCodeRuntime, "command", lambda runtime, config, session, resume:
                                       [sys.executable, str(FAKE_CLAUDE)] + real(runtime, config, session, resume)[1:])
        self.which = mock.patch("quantarium.runtimes.shutil.which", return_value="claude")

    def connect(self) -> dict:
        return self.api.command("agent-connection-create", {
            "name": "Claude via Claude Code", "kind": "bridge",
            "provider": {"runtime": "claude-code", "acknowledgement": self.ack}})["connection"]

    def test_the_acknowledgement_is_required_and_recorded(self) -> None:
        status, body = None, None
        try:
            self.api.command("agent-connection-create", {"name": "No ack", "kind": "bridge",
                                                         "provider": {"runtime": "claude-code"}})
        except Exception as exc:  # the API raises for invalid parameters
            body = str(exc)
        self.assertIn("acknowledgement", body or "")
        connection = self.connect()
        self.assertTrue(connection["acknowledged_utc"])
        self.assertEqual(connection["profile"], "claude-code")

    def test_two_turns_through_claude_code(self) -> None:
        connection = self.connect()
        with self.patch, self.which, mock.patch.dict(os.environ, {"FAKE_CLAUDE_RECORD": str(self.record),
                                                                  "ANTHROPIC_API_KEY": "sk-ant-secret"}):
            chat = self.api.command("chat-create", {"connection_id": connection["id"]})
            first = self.turn(chat["id"], "What cells do I have?")
            second = self.turn(chat["id"], "And now?")
        self.assertEqual(first["status"], "idle", first.get("error"))
        self.assertEqual(self.texts(second), ["You have 1 cells.", "Again!"])
        tools = [block["name"] for message in second["messages"] for block in message["content"]
                 if block["type"] == "tool_use"]
        self.assertEqual(tools, ["list_cells", "list_cells"])
        self.assertEqual(second["usage"], {"input_tokens": 500, "output_tokens": 40})

        first_run, second_run = self.runs()
        args = first_run["args"]
        self.assertEqual(args[:5], ["-p", "--output-format", "stream-json", "--verbose", "--mcp-config"])
        # No built-in tools, only Quantarium's server, only Quantarium's tools allowed, no user settings.
        self.assertEqual(args[args.index("--tools") + 1], "")
        self.assertIn("--strict-mcp-config", args)
        self.assertEqual(args[args.index("--allowedTools") + 1], "mcp__quantarium")
        self.assertEqual(args[args.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(args[args.index("--setting-sources") + 1], "project,local")
        self.assertIn("--disable-slash-commands", args)
        self.assertNotIn("--dangerously-skip-permissions", args)
        session = second["bridge"]["session_id"]
        self.assertEqual(args[args.index("--session-id") + 1], session)
        self.assertEqual(second_run["args"][second_run["args"].index("--resume") + 1], session)
        # The token is in the per-turn config file, never on the command line, and both go away.
        self.assertNotIn(first_run["token"], " ".join(args))
        self.assertFalse(Path(first_run["config"]).exists())
        self.assertEqual((first_run["status"], first_run["env"]), (200, []))
        self.assertEqual(self.bearer_status(first_run["token"]), 401)
        self.assertEqual(first_run["prompt"], "What cells do I have?")

    def test_offering_another_tool_stops_the_turn(self) -> None:
        connection = self.connect()
        started = time.monotonic()
        with self.patch, self.which, mock.patch.dict(os.environ, {"FAKE_CLAUDE_RECORD": str(self.record),
                                                                  "FAKE_CLAUDE_MODE": "rogue"}):
            chat = self.api.command("chat-create", {"connection_id": connection["id"]})
            done = self.turn(chat["id"], "Hello")
        self.assertLess(time.monotonic() - started, 20)
        self.assertTrue(any("Claude Code offered or used a tool outside Quantarium's (Bash)" in text
                            for text in self.texts(done)), self.texts(done))

    def test_deleting_a_chat_can_remove_claude_codes_transcript(self) -> None:
        connection = self.connect()
        home = self.root / "claude-home"
        with self.patch, self.which, mock.patch.dict(os.environ, {"FAKE_CLAUDE_RECORD": str(self.record),
                                                                  "CLAUDE_CONFIG_DIR": str(home)}):
            chat = self.api.command("chat-create", {"connection_id": connection["id"]})
            done = self.turn(chat["id"], "Hello")
            transcript = home / "projects" / "C--Temp-quantarium-turn-x" / f"{done['bridge']['session_id']}.jsonl"
            transcript.parent.mkdir(parents=True)
            transcript.write_text("{}\n", encoding="utf-8")
            listed = self.api.get(f"agents/chats/{chat['id']}", {})
            self.assertEqual((listed["app_profile"], listed["app_transcripts"]), ("claude-code", [str(transcript)]))
            result = self.api.command("chat-delete", {"id": chat["id"], "app_transcript": True})
        self.assertEqual((result["folder_removed"], result["app_transcripts_removed"]), (True, 1))
        self.assertFalse(transcript.exists())

    def test_an_error_already_shown_as_a_reply_is_not_repeated(self) -> None:
        chat = {"messages": [], "usage": {"input_tokens": 0, "output_tokens": 0}, "bridge": {}}
        ClaudeCodeRuntime._apply({"type": "assistant", "message": {"content": [{"type": "text", "text": "Not signed in"}]}},
                                 chat, {})
        ClaudeCodeRuntime._apply({"type": "result", "is_error": True, "result": "Not signed in"}, chat, {})
        ClaudeCodeRuntime._apply({"type": "result", "is_error": True, "result": "Rate limited"}, chat, {})
        self.assertEqual(self.texts(chat), ["Not signed in", "Claude Code reported an error: Rate limited"])

    def test_without_claude_code_installed(self) -> None:
        connection = self.connect()
        with mock.patch("quantarium.runtimes.shutil.which", return_value=None):
            chat = self.api.command("chat-create", {"connection_id": connection["id"]})
            done = self.turn(chat["id"], "Hello")
        self.assertEqual(done["status"], "error")
        self.assertIn("Claude Code is not installed", done["error"])


if __name__ == "__main__":
    unittest.main()
