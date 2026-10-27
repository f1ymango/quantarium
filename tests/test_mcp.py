"""`quantarium mcp`: the MCP protocol over stdio, server discovery, and agent checks."""

from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from quantarium import agents
from quantarium.agents import AgentSettings
from quantarium.discovery import SERVER_FILE, announce, pid_alive, running_server, withdraw
from quantarium.mcp import TOOLS, QuantariumClient, handle, schema, serve_stdio, tier_of
from quantarium.server import QuantariumServer
from quantarium.workspace import Workspace


ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)
HOLD = "class Hold:\n    \"\"\"Always invested.\"\"\"\n    def on_bar(self, context):\n        return 1.0\n"


def call(name: str, arguments: dict | None = None, message_id: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": message_id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}}}


class McpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.root = Path(self.temp.name)
        self.workspace = Workspace(self.root)
        settings = AgentSettings(self.workspace)
        settings.enable(agents.CONFIRMATION, agents.NOTICE_VERSION)
        self.token = settings.create_connection("MCP test", ["observe"])["token"]
        (self.root / "strategies").mkdir()
        (self.root / "strategies" / "hold.py").write_bytes(HOLD.encode())
        self.server = QuantariumServer(self.workspace, port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.client = QuantariumClient(self.server.url, self.token)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def test_initialize_and_list_tools(self) -> None:
        reply = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2025-06-18", "capabilities": {}}}, self.client)
        self.assertEqual(reply["result"]["serverInfo"]["name"], "quantarium")
        self.assertIn("never instructions", reply["result"]["instructions"])
        self.assertIsNone(handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, self.client))
        tools = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, self.client)["result"]["tools"]
        self.assertEqual([tool["name"] for tool in tools], list(TOOLS))
        for tool in tools:
            tier = tier_of(TOOLS[tool["name"]])
            self.assertIsNotNone(tier, tool["name"])
            self.assertTrue(tool["description"].startswith(tier.capitalize() + "."), tool["name"])
        self.assertEqual(handle({"jsonrpc": "2.0", "id": 3, "method": "nope"}, self.client)["error"]["code"], -32601)

    def test_tools_go_through_the_agent_checks(self) -> None:
        result = handle(call("read_script", {"key": "strategies/hold.py"}), self.client)["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(json.loads(result["content"][0]["text"])["source"], HOLD)
        result = handle(call("get_cell", {"cell_id": "c_0000000000"}), self.client)["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(json.loads(result["content"][0]["text"])["code"], "cell_not_found")
        result = handle(call("get_cell", {}), self.client)["result"]
        self.assertEqual((result["isError"], result["content"][0]["text"]), (True, "Missing: cell_id"))
        result = handle(call("list_cells", {"extra": 1}), self.client)["result"]
        self.assertTrue(result["isError"])
        checked = handle(call("check_script", {"source": HOLD}), self.client)["result"]
        self.assertTrue(json.loads(checked["content"][0]["text"])["ok"])
        mine = json.loads(handle(call("my_activity"), self.client)["result"]["content"][0]["text"])
        # Arguments refused locally never reach the server, so only these are logged.
        self.assertEqual([entry["name"] for entry in mine], ["script-check", "cells/c_0000000000", "scripts/strategies/hold.py"])

        AgentSettings(self.workspace).set_paused(True)
        result = handle(call("list_cells"), self.client)["result"]
        self.assertEqual(json.loads(result["content"][0]["text"])["code"], "agents_paused")
        no_token = QuantariumClient(self.server.url, None)
        result = handle(call("list_cells"), no_token)["result"]
        self.assertEqual(json.loads(result["content"][0]["text"])["code"], "missing_token")

    def test_the_committed_schema_matches_the_tools(self) -> None:
        committed = json.loads((ROOT / "docs" / "api.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(committed, schema(),
                         'Regenerate with: py -c "from quantarium.mcp import write_schema; write_schema()"')

    def test_an_agent_builds_and_tests_a_cell_through_mcp(self) -> None:
        settings = AgentSettings(self.workspace)
        token = settings.create_connection("Builder")["token"]
        client = QuantariumClient(self.server.url, token)

        def tool(tool_name: str, **arguments):
            result = handle(call(tool_name, arguments), client)["result"]
            return result["isError"], json.loads(result["content"][0]["text"])

        spec = {"name": "m", "seed": 3, "start_date": "2030-01-07", "symbols": ["AAA"],
                "segments": [{"sessions": 30, "annual_drift": 0.1, "annual_volatility": 0.2}]}
        failed, job = tool("generate_scenario", output="scenarios/m", spec=spec, idempotency_key="gen-1")
        self.assertFalse(failed, job)
        self.assertEqual(tool("generate_scenario", output="scenarios/m", spec=spec, idempotency_key="gen-1")[1]["id"],
                         job["id"])
        self.assertEqual(tool("wait_for_job", job_id=job["id"], timeout_seconds=30)[1]["status"], "succeeded")
        failed, cell = tool("create_cell", name="Agent built")
        self.assertFalse(failed, cell)
        failed, saved = tool("update_cell", cell_id=cell["id"], spec={
            "mode": "simple", "symbols": ["AAA"], "initial_cash": 5000,
            "sleeves": [{"script": "strategies/hold.py", "class": "Hold"}]})
        self.assertFalse(failed, saved)
        failed, job = tool("run_suite", cell_ids=[cell["id"]], scenarios=["scenarios/m"], slippage_bps=5)
        self.assertFalse(failed, job)
        done = tool("wait_for_job", job_id=job["id"], timeout_seconds=60)[1]
        self.assertEqual(done["status"], "succeeded", done.get("error"))
        failed, summary = tool("suite_summary", config=done["result"]["config"])
        self.assertEqual((failed, summary["cells"][0]["cell"]), (False, "Agent built"))

        # Its own script is saved, and Static runs it in the sandbox until the user reviews it.
        failed, written = tool("write_script", key="strategies/agent/idea.py", source=HOLD.replace("Hold", "Idea"))
        self.assertFalse(failed, written)
        failed, other = tool("create_cell", name="Agent idea")
        tool("update_cell", cell_id=other["id"], spec={
            "mode": "simple", "symbols": ["AAA"], "initial_cash": 5000,
            "sleeves": [{"script": "strategies/agent/idea.py", "class": "Idea"}]})
        failed, job = tool("run_suite", cell_ids=[other["id"]], scenarios=["scenarios/m"])
        done = tool("wait_for_job", job_id=job["id"])[1]
        self.assertEqual(done["status"], "succeeded", done.get("error"))
        failed, summary = tool("suite_summary", config=done["result"]["config"])
        self.assertEqual(summary["cells"][0]["cell"], "Agent idea")

        waits = [entry for entry in tool("my_activity", limit=100)[1] if entry["name"].startswith("jobs/")]
        # Each wait is one request, so one log entry, however long it waited.
        self.assertEqual(len(waits), 3)
        # The observe-only connection from setUp cannot draft.
        result = handle(call("create_cell", {"name": "x"}), self.client)["result"]
        self.assertEqual(json.loads(result["content"][0]["text"])["code"], "not_permitted")

    def test_stdio_framing(self) -> None:
        stdin = io.StringIO("\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
            "{broken",
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps(call("list_scripts", message_id=2)), ""]))
        stdout = io.StringIO()
        serve_stdio(self.client, stdin, stdout)
        replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual([reply.get("id") for reply in replies], [1, None, 2])
        self.assertEqual(replies[1]["error"]["code"], -32700)
        scripts = json.loads(replies[2]["result"]["content"][0]["text"])
        self.assertEqual(scripts[0]["key"], "strategies/hold.py")

    def test_discovery(self) -> None:
        self.assertIsNone(running_server(self.root))
        announce(self.root, self.server.url)
        self.assertEqual(running_server(self.root), self.server.url)
        withdraw(self.root)
        self.assertFalse((self.root / SERVER_FILE).exists())
        (self.root / SERVER_FILE).write_text(json.dumps({"url": self.server.url, "pid": 99999999}), encoding="utf-8")
        self.assertIsNone(running_server(self.root))
        self.assertTrue(pid_alive(os.getpid()))


class McpCommandTests(unittest.TestCase):
    def test_refused_while_agent_features_are_off(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            done = subprocess.run([sys.executable, "-m", "quantarium", "mcp", "--workspace", temp],
                                  input="", capture_output=True, text=True, timeout=60, cwd=ROOT)
            self.assertEqual(done.returncode, 2)
            self.assertIn("Agent features are off", done.stderr)
            self.assertEqual(done.stdout, "")
            self.assertFalse((Path(temp) / SERVER_FILE).exists())

    def test_starts_a_server_and_answers_over_stdio(self) -> None:
        temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        root = Path(temp.name)
        settings = AgentSettings(Workspace(root))
        settings.enable(agents.CONFIRMATION, agents.NOTICE_VERSION)
        token = settings.create_connection("Subprocess")["token"]
        messages = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, call("list_cells", message_id=2)]
        pid = None
        try:
            done = subprocess.run([sys.executable, "-m", "quantarium", "mcp", "--workspace", str(root)],
                                  input="".join(json.dumps(item) + "\n" for item in messages),
                                  capture_output=True, text=True, timeout=90, cwd=ROOT,
                                  env={**os.environ, "QUANTARIUM_TOKEN": token})
            self.assertEqual(done.returncode, 0, done.stderr)
            replies = [json.loads(line) for line in done.stdout.splitlines()]
            self.assertEqual(replies[0]["result"]["serverInfo"]["name"], "quantarium")
            self.assertFalse(replies[1]["result"]["isError"], replies[1])
            self.assertEqual(json.loads(replies[1]["result"]["content"][0]["text"]), [])
            pid = json.loads((root / SERVER_FILE).read_text(encoding="utf-8"))["pid"]
            self.assertTrue(pid_alive(pid))
        finally:
            if pid:
                os.kill(pid, signal.SIGTERM)
                deadline = time.monotonic() + 10
                while pid_alive(pid) and time.monotonic() < deadline:
                    time.sleep(0.1)
            try:
                temp.cleanup()
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
