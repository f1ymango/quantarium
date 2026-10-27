"""A stand-in ACP agent for tests: speaks JSON-RPC on stdio like an Agent Client
Protocol agent, calls Quantarium with the token from the MCP server Quantarium passed,
asks permission for tools, and records what Quantarium sent and answered.

FAKE_ACP_MODE picks the behaviour: "normal", "noload" (no loadSession
capability), "rogue" (reports a shell tool without asking), or "slow" (waits
until Quantarium cancels)."""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

MODE = os.environ.get("FAKE_ACP_MODE", "normal")
# How the agent names Quantarium's tool: Claude-style by default, or OpenCode's server_tool.
TITLE = os.environ.get("FAKE_ACP_TOOL_TITLE", "mcp__quantarium__list_cells")
RECORD = Path(os.environ.get("FAKE_ACP_RECORD", "fake_acp_record.jsonl"))
servers: list = []
next_id = 1000


def record(entry: dict) -> None:
    with RECORD.open("a", encoding="utf-8") as output:
        output.write(json.dumps(entry) + "\n")


def send(message: dict) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
    sys.stdout.flush()


def update(session: str, body: dict) -> None:
    send({"method": "session/update", "params": {"sessionId": session, "update": body}})


def ask(method: str, params: dict) -> dict:
    """Send a request to Quantarium and wait for its answer."""
    global next_id
    next_id += 1
    wanted = next_id
    send({"id": wanted, "method": method, "params": params})
    for line in sys.stdin:
        message = json.loads(line)
        if message.get("id") == wanted and "method" not in message:
            return message
        if message.get("method") == "session/cancel":
            record({"cancel": True})


def call_quantarium() -> tuple[int, list]:
    quantarium = next(item for item in servers if item["name"] == "quantarium")
    token = next(item["value"] for item in quantarium["env"] if item["name"] == "QUANTARIUM_TOKEN")
    workspace = Path(quantarium["args"][-1])
    url = json.loads((workspace / ".quantarium" / "server.json").read_text(encoding="utf-8"))["url"]
    request = urllib.request.Request(url.rstrip("/") + "/api/v1/cells", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        error.close()
        return error.code, []


def prompt(message: dict) -> None:
    params = message["params"]
    session = params["sessionId"]
    text = params["prompt"][0]["text"]
    record({"prompt": text})
    if MODE == "rogue":
        update(session, {"sessionUpdate": "tool_call", "toolCallId": "x1", "title": "Shell: type agents.json",
                         "kind": "execute", "status": "in_progress"})
        time.sleep(30)
        return
    if MODE == "slow":
        for line in sys.stdin:
            if json.loads(line).get("method") == "session/cancel":
                record({"cancel": True})
                send({"id": message["id"], "result": {"stopReason": "cancelled"}})
                return
        return
    update(session, {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": TITLE,
                     "kind": "other", "status": "pending", "rawInput": {}})
    allow = ask("session/request_permission", {"sessionId": session, "toolCall": {
        "toolCallId": "t1", "title": TITLE, "kind": "other"},
        "options": [{"optionId": "yes", "name": "Allow", "kind": "allow_once"},
                    {"optionId": "no", "name": "Reject", "kind": "reject_once"}]})
    status, cells = call_quantarium()
    update(session, {"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed",
                     "content": [{"type": "content", "content": {"type": "text", "text": json.dumps(cells)}}]})
    shell = ask("session/request_permission", {"sessionId": session, "toolCall": {
        "toolCallId": "t2", "title": "Run rm -rf strategies", "kind": "execute"},
        "options": [{"optionId": "yes", "name": "Allow", "kind": "allow_once"},
                    {"optionId": "no", "name": "Reject", "kind": "reject_once"}]})
    read = ask("fs/read_text_file", {"sessionId": session, "path": ".quantarium/agents.json"})
    record({"allow": allow.get("result"), "shell": shell.get("result"), "read": read.get("error"),
            "status": status, "env": sorted(key for key in os.environ if key.startswith(("QUANTARIUM", "ANTHROPIC"))),
            "opencode_config": os.environ.get("OPENCODE_CONFIG_CONTENT")})
    update(session, {"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "thinking"}})
    update(session, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "You have "}})
    update(session, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": f"{len(cells)} cells."}})
    send({"id": message["id"], "result": {"stopReason": "end_turn", "usage": {"inputTokens": 100, "outputTokens": 7}}})


for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method == "initialize":
        record({"initialize": message["params"], "cwd": os.getcwd()})
        send({"id": message["id"], "result": {"protocolVersion": 1,
                                              "agentCapabilities": {"loadSession": MODE != "noload"}}})
    elif method == "session/new":
        servers = message["params"]["mcpServers"]
        record({"new": message["params"]})
        send({"id": message["id"], "result": {"sessionId": "sess-1"}})
    elif method == "session/load":
        servers = message["params"]["mcpServers"]
        record({"load": message["params"]["sessionId"]})
        update("sess-1", {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "OLD HISTORY"}})
        send({"id": message["id"], "result": None})
    elif method == "session/prompt":
        prompt(message)
