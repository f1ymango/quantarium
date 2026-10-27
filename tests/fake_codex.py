"""A stand-in for `codex exec --json` in tests: records how it was run, calls
Quantarium with the per-turn token like Quantarium's MCP server would, and prints
Codex-style JSONL events."""

import json
import os
import sys
import urllib.request
from pathlib import Path

args = sys.argv[1:]
prompt = sys.stdin.read()
settings = [args[index + 1] for index, value in enumerate(args) if value == "-c"]
token = next(item for item in settings if item.startswith("mcp_servers.quantarium.env=")).split('"')[1]
workspace = Path(json.loads(next(item for item in settings if item.startswith("mcp_servers.quantarium.args="))
                            .split("=", 1)[1])[-1])
server = json.loads((workspace / ".quantarium" / "server.json").read_text(encoding="utf-8"))["url"]
request = urllib.request.Request(server.rstrip("/") + "/api/v1/cells", headers={"Authorization": f"Bearer {token}"})
try:
    with urllib.request.urlopen(request) as response:
        status, cells = response.status, json.loads(response.read())
except urllib.error.HTTPError as error:
    status, cells = error.code, []
record = Path(os.environ.get("FAKE_CODEX_RECORD", "fake_codex_record.jsonl"))
with record.open("a", encoding="utf-8") as output:
    output.write(json.dumps({"args": args, "prompt": prompt, "token": token, "status": status, "cwd": os.getcwd(),
                             "env": sorted(key for key in os.environ if key.startswith(("QUANTARIUM", "ANTHROPIC")))}) + "\n")
resume = "resume" in args
if "run a command" in prompt:
    print(json.dumps({"type": "thread.started", "thread_id": "99999999-2222-3333-4444-555555555555"}), flush=True)
    print(json.dumps({"type": "item.started", "item": {"id": "item_9", "type": "command_execution",
                                                       "command": "type .quantarium\\agents.json", "status": "in_progress"}}),
          flush=True)
    import time
    time.sleep(30)  # Quantarium should stop the turn long before this
    print(json.dumps({"type": "item.completed", "item": {"id": "item_10", "type": "agent_message", "text": "leaked"}}))
    sys.exit(0)
events = [] if resume else [{"type": "thread.started", "thread_id": "11111111-2222-3333-4444-555555555555"}]
events += [
    {"type": "turn.started"},
    {"type": "item.completed", "item": {"id": "item_1", "type": "mcp_tool_call", "server": "quantarium",
                                        "tool": "list_cells", "arguments": {}, "status": "completed",
                                        "result": {"content": [{"type": "text", "text": json.dumps(cells)}]}}},
    {"type": "item.completed", "item": {"id": "item_2", "type": "agent_message",
                                        "text": "Again!" if resume else f"You have {len(cells)} cells."}},
    {"type": "turn.completed", "usage": {"input_tokens": 300, "cached_input_tokens": 0, "output_tokens": 20}},
]
for event in events:
    print(json.dumps(event), flush=True)
