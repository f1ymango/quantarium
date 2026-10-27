"""A stand-in for `claude -p --output-format stream-json` in tests: records how it
was run, calls Quantarium with the token from the per-turn MCP config file like
Quantarium's MCP server would, and prints Claude Code-style stream-json events.

FAKE_CLAUDE_MODE "rogue" reports a built-in tool at start, as if --tools
had been ignored."""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

args = sys.argv[1:]
prompt = sys.stdin.read()
config_path = Path(args[args.index("--mcp-config") + 1])
config = json.loads(config_path.read_text(encoding="utf-8"))
quantarium = config["mcpServers"]["quantarium"]
token = quantarium["env"]["QUANTARIUM_TOKEN"]
workspace = Path(quantarium["args"][-1])
url = json.loads((workspace / ".quantarium" / "server.json").read_text(encoding="utf-8"))["url"]
request = urllib.request.Request(url.rstrip("/") + "/api/v1/cells", headers={"Authorization": f"Bearer {token}"})
try:
    with urllib.request.urlopen(request) as response:
        status, cells = response.status, json.loads(response.read())
except urllib.error.HTTPError as error:
    error.close()
    status, cells = error.code, []
session = args[args.index("--resume" if "--resume" in args else "--session-id") + 1]
with Path(os.environ["FAKE_CLAUDE_RECORD"]).open("a", encoding="utf-8") as output:
    output.write(json.dumps({"args": args, "prompt": prompt, "token": token, "status": status, "config": str(config_path),
                             "cwd": os.getcwd(), "env": sorted(key for key in os.environ
                                                               if key.startswith(("QUANTARIUM", "ANTHROPIC")))}) + "\n")
tools = ["mcp__quantarium__list_cells", "mcp__quantarium__get_cell"]
if os.environ.get("FAKE_CLAUDE_MODE") == "rogue":
    tools.append("Bash")
events = [
    {"type": "system", "subtype": "init", "session_id": session, "tools": tools,
     "mcp_servers": [{"name": "quantarium", "status": "connected"}]},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_1",
                                                   "name": "mcp__quantarium__list_cells", "input": {}}]}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_1",
                                              "content": [{"type": "text", "text": json.dumps(cells)}]}]}},
    {"type": "assistant", "message": {"content": [{"type": "text",
                                                   "text": "Again!" if "--resume" in args else f"You have {len(cells)} cells."}]}},
    {"type": "result", "subtype": "success", "is_error": False, "session_id": session, "result": "done",
     "usage": {"input_tokens": 50, "cache_read_input_tokens": 200, "cache_creation_input_tokens": 0,
               "output_tokens": 20}},
]
for event in events:
    print(json.dumps(event), flush=True)
    if os.environ.get("FAKE_CLAUDE_MODE") == "rogue":
        import time
        time.sleep(30)  # Quantarium should stop the turn long before this
