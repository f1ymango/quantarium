"""The built-in assistant: its turn loop, its limits, and parity with MCP agents."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from quantarium import agents, assistant
from quantarium.agents import AgentSettings
from quantarium.assistant import FakeProvider
from quantarium.mcp import QuantariumClient, handle
from quantarium.server import QuantariumServer
from quantarium.workspace import Workspace


ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)


def tool_use(call_id: str, tool_name: str, **arguments) -> dict:
    return {"content": [{"type": "tool_use", "id": call_id, "name": tool_name, "input": arguments}],
            "usage": {"input_tokens": 100, "output_tokens": 10}}


def text(value: str) -> dict:
    return {"content": [{"type": "text", "text": value}], "usage": {"input_tokens": 50, "output_tokens": 5}}


class AssistantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.root = Path(self.temp.name)
        self.server = QuantariumServer(Workspace(self.root), port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.api = self.server.api
        self.settings = AgentSettings(self.api.workspace)
        self.settings.enable(agents.CONFIRMATION, agents.NOTICE_VERSION)
        self.connection = self.connect("Assistant")

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def connect(self, name: str, tiers: list | None = None) -> dict:
        created = self.api.command("agent-connection-create", {
            "name": name, "kind": "direct", "tiers": tiers,
            "provider": {"provider": "anthropic", "model": "test-model", "key_env": "TEST_KEY"}})
        self.assertIsNone(created["token"])
        return created["connection"]

    def chat(self, provider: FakeProvider, message: str = "Make me a cell", connection: dict | None = None) -> dict:
        self.api.assistant.provider_for = lambda connection: provider
        chat = self.api.command("chat-create", {"connection_id": (connection or self.connection)["id"]})
        self.api.command("chat-send", {"id": chat["id"], "text": message, "page": "#/home"})
        deadline = time.monotonic() + 30
        while self.api.assistant.busy(chat["id"]) and time.monotonic() < deadline:
            time.sleep(0.02)
        return self.api.get(f"agents/chats/{chat['id']}", {})

    def test_a_turn_runs_tools_through_the_agent_path(self) -> None:
        provider = FakeProvider([tool_use("t1", "list_cells"), tool_use("t2", "create_cell", name="From chat"),
                                 text("Made it: [[cell:c_x]]")])
        chat = self.chat(provider)
        self.assertEqual((chat["status"], chat["running"], chat["title"]), ("idle", False, "Make me a cell"))
        roles = [(message["role"], message["content"][0]["type"]) for message in chat["messages"]]
        self.assertEqual(roles, [("user", "text"), ("assistant", "tool_use"), ("user", "tool_result"),
                                 ("assistant", "tool_use"), ("user", "tool_result"), ("assistant", "text")])
        self.assertEqual(chat["messages"][0]["content"][1]["text"], "(The user is on the Quantarium page #/home.)")
        created = json.loads(chat["messages"][4]["content"][0]["content"])
        self.assertEqual(created["name"], "From chat")
        self.assertEqual(chat["usage"], {"input_tokens": 250, "output_tokens": 25})
        log = self.api.get("agents/log", {})
        self.assertEqual([(entry["connection_name"], entry["name"], entry["outcome"]) for entry in log],
                         [("Assistant", "cell-create", "ok"), ("Assistant", "cells", "ok")])
        # The model saw the system prompt and only the tools its grant allows.
        request = provider.requests[0]
        self.assertIn("You cannot approve proposals", request["system"])
        names = {tool["name"] for tool in request["tools"]}
        self.assertIn("create_cell", names)
        self.assertIn("propose_advance", names)

    def test_tools_follow_the_grant_and_refusals_reach_the_model(self) -> None:
        reader = self.connect("Reader", ["observe"])
        provider = FakeProvider([tool_use("t1", "create_cell", name="x"), tool_use("t2", "no_such_tool"),
                                 tool_use("t3", "get_cell"), text("Sorry")])
        chat = self.chat(provider, connection=reader)
        offered = {tool["name"] for tool in provider.requests[0]["tools"]}
        self.assertNotIn("create_cell", offered)
        self.assertIn("list_cells", offered)
        results = [message["content"][0] for message in chat["messages"] if message["content"][0]["type"] == "tool_result"]
        self.assertTrue(all(result["is_error"] for result in results))
        self.assertEqual(json.loads(results[0]["content"])["code"], "not_permitted")
        self.assertEqual(results[1]["content"], "Unknown tool: no_such_tool")
        self.assertEqual(results[2]["content"], "Missing: cell_id")
        self.assertEqual(self.api.get("cells", {}), [])

    def test_the_assistant_can_propose_but_never_approve(self) -> None:
        cell = self.api.command("cell-create", {"name": "Old"})
        provider = FakeProvider([tool_use("t1", "propose_archive", cell_ids=[cell["id"]], rationale="Unused"),
                                 text("Proposed [[proposal:p]]")])
        self.chat(provider)
        offered = {tool["name"] for tool in provider.requests[0]["tools"]}
        self.assertFalse({"proposal_decide", "approve"} & offered)
        self.assertEqual([item["status"] for item in self.api.get("agents/proposals", {})], ["pending"])
        self.assertFalse(self.api.get(f"cells/{cell['id']}", {})["archived"])

    def test_a_runaway_turn_stops_and_off_means_off(self) -> None:
        with mock.patch.object(assistant, "MAX_STEPS", 3):
            chat = self.chat(FakeProvider([tool_use(f"t{index}", "list_cells") for index in range(10)]))
        self.assertIn("I stopped after 3 steps", chat["messages"][-1]["content"][0]["text"])
        self.settings.disable()
        with self.assertRaises(agents.AgentsDisabled):
            self.api.command("chat-create", {"connection_id": self.connection["id"]})

    def test_parity_with_an_mcp_agent(self) -> None:
        """The same tool through MCP and through the assistant: same operation, outcome, and result."""
        external = self.settings.create_connection("External")
        client = QuantariumClient(self.server.url, external["token"])
        mcp_result = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                             "params": {"name": "list_scripts", "arguments": {}}}, client)["result"]
        chat = self.chat(FakeProvider([tool_use("t1", "list_scripts"), text("ok")]))
        assistant_result = chat["messages"][2]["content"][0]
        self.assertEqual(json.loads(assistant_result["content"]), json.loads(mcp_result["content"][0]["text"]))
        log = self.api.get("agents/log", {})
        self.assertEqual({(entry["kind"], entry["name"], entry["outcome"]) for entry in log},
                         {("get", "scripts", "ok")})
        self.assertEqual({entry["connection_name"] for entry in log}, {"External", "Assistant"})


if __name__ == "__main__":
    unittest.main()
