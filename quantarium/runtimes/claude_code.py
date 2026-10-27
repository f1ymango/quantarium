"""The ``claude-code`` profile: the assistant through the user's own Claude Code. Experimental.

Quantarium runs the user's installed, unmodified ``claude`` CLI in headless mode
(``-p --output-format stream-json``). Claude Code signs in by itself, with
whatever the user set up there: a Claude plan or an API key. Quantarium never
reads, stores, or passes on that sign-in, and removes none of its sign-in
methods. Anthropic's terms allow an end user to sign in to the unmodified
Claude Code with their own plan; whether to do so is the user's decision,
which they acknowledge when creating the connection.

Each turn Claude Code gets:

- no built-in tools (``--tools ""``), so no shell, file, web, or edit tools;
- only Quantarium's MCP server (``--strict-mcp-config``), from a per-turn
  config file that holds the turn's token, so the token is not on the
  command line;
- permission for Quantarium's tools only (``--allowedTools mcp__quantarium`` with
  ``--permission-mode dontAsk``, which refuses everything else);
- no user settings (``--setting-sources project,local`` in an empty folder),
  so the user's hooks and other MCP servers are not attached.

If Claude Code still reports a tool that is not Quantarium's, Quantarium stops the
turn. Claude Code loads the user's own ``~/.claude/CLAUDE.md``, as Codex
loads ``~/.codex/AGENTS.md``; Quantarium's limits don't depend on it.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path

from ..errors import QuantariumError
from . import UUID, finish, first_turn, mcp_server, resolve, say, start, tool_exchange, turn_environment, \
    turn_folder, user_text, watch

CLAUDE = "claude"
SERVER = "quantarium"
TOOL_PREFIX = f"mcp__{SERVER}__"
# Claude Code's own tools for reading MCP resources; with only Quantarium attached they read only Quantarium.
MCP_RESOURCE_TOOLS = {"ListMcpResourcesTool", "ReadMcpResourceTool"}
CONFIG_NAME = ".quantarium-mcp.json"
SYSTEM = first_turn("Claude Code")


class ClaudeCodeRuntime:
    def __init__(self, connection: dict) -> None:
        self.connection = connection

    def command(self, config: str, session_id: str, resume: bool) -> list[str]:
        executable = resolve(CLAUDE, "Claude Code is not installed or not on PATH",
                             "Install Claude Code and sign in by running claude, then restart Quantarium")
        if not UUID.fullmatch(session_id):
            raise QuantariumError("This chat's Claude Code session id is invalid; start a new chat", "bridge_failed")
        return [executable, "-p", "--output-format", "stream-json", "--verbose",
                "--mcp-config", config, "--strict-mcp-config",
                "--tools", "", "--allowedTools", f"mcp__{SERVER}", "--permission-mode", "dontAsk",
                "--setting-sources", "project,local", "--disable-slash-commands",
                "--append-system-prompt", SYSTEM,
                "--resume" if resume else "--session-id", session_id]

    def run_turn(self, api, chat: dict, connection: dict, stop: threading.Event, save) -> None:
        state = chat.setdefault("bridge", {})
        state["profile"] = "claude-code"
        if not UUID.fullmatch(str(state.get("session_id") or "")):
            state["session_id"], state["started"] = str(uuid.uuid4()), False
        folder = turn_folder(state)
        token = api.agents.issue_session_token(connection["id"])
        config = Path(folder) / CONFIG_NAME
        python, args = mcp_server(api)
        process = None
        try:
            config.write_text(json.dumps({"mcpServers": {SERVER: {
                "type": "stdio", "command": python, "args": args, "env": {"QUANTARIUM_TOKEN": token}}}}),
                encoding="utf-8")
            process = start(self.command(str(config), state["session_id"], state.get("started") is True),
                            folder, turn_environment(api))
            process.stdin.write(user_text(chat))
            process.stdin.close()
            watch(process, stop)
            calls: dict[str, dict] = {}
            finished = False
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                outside = _outside_tool(event)
                if outside:
                    process.kill()
                    say(chat, f"Quantarium stopped this turn: Claude Code offered or used a tool outside Quantarium's "
                        f"({outside[:200]}). Quantarium allows only its own tools in these chats.")
                    save(chat)
                    stop.set()
                    break
                finished = finished or event.get("type") == "result"
                if self._apply(event, chat, calls):
                    save(chat)
            process.wait(timeout=30)
            if process.returncode not in (0, None) and not stop.is_set() and not finished:
                detail = (process.stderr.read() or "")[-500:].strip()
                raise QuantariumError(f"Claude Code stopped with status {process.returncode}: {detail}",
                                   "bridge_failed", "Run claude once in a terminal to check that it is signed in")
        finally:
            finish(process)
            config.unlink(missing_ok=True)
            api.agents.end_session_token(connection["id"])

    @staticmethod
    def _apply(event: dict, chat: dict, calls: dict) -> bool:
        """Fold one Claude Code event into the chat; True when the chat changed."""
        kind = event.get("type")
        state = chat["bridge"]
        if kind == "system" and event.get("subtype") == "init":
            state["started"] = True
            servers = {item.get("name"): item.get("status") for item in event.get("mcp_servers") or []
                       if isinstance(item, dict)}
            if servers.get(SERVER) not in {None, "connected"}:
                say(chat, f"Claude Code could not start Quantarium's tools ({servers.get(SERVER)}).")
            return True
        if kind == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if block.get("type") == "text" and block.get("text"):
                    say(chat, block["text"])
                elif block.get("type") == "tool_use":
                    calls[str(block.get("id"))] = {"name": str(block.get("name") or "")[len(TOOL_PREFIX):] or "tool",
                                                   "input": block.get("input") or {}}
            return True
        if kind == "user":
            changed = False
            for block in (event.get("message") or {}).get("content") or []:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                call_id = str(block.get("tool_use_id"))
                call = calls.pop(call_id, {"name": "tool", "input": {}})
                content = block.get("content")
                if isinstance(content, list):
                    content = "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
                tool_exchange(chat, call_id, call["name"], call["input"], str(content or ""), block.get("is_error") is True)
                changed = True
            return changed
        if kind == "result":
            usage = event.get("usage") or {}
            chat["usage"]["input_tokens"] += sum(int(usage.get(key) or 0) for key in (
                "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            chat["usage"]["output_tokens"] += int(usage.get("output_tokens") or 0)
            if event.get("is_error"):
                text = str(event.get("result") or event.get("subtype"))[:500]
                last = chat["messages"][-1] if chat["messages"] else {}
                # Claude Code often sends the error as a reply first; say it once.
                if not (last.get("role") == "assistant"
                        and any(block.get("text") == text for block in last.get("content", []))):
                    say(chat, f"Claude Code reported an error: {text}")
            return True
        return False

    @staticmethod
    def transcripts(state: dict) -> list[Path]:
        """Claude Code's own transcript of the chat, in its projects folder."""
        session = state.get("session_id")
        if not isinstance(session, str) or not UUID.fullmatch(session):
            return []
        home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
        projects = home / "projects"
        if not projects.is_dir():
            return []
        return sorted(path for path in projects.glob(f"*/{session}.jsonl") if path.is_file())


def _outside_tool(event: dict) -> str | None:
    """A tool that is not Quantarium's, offered at start or used in a reply."""
    if event.get("type") == "system" and event.get("subtype") == "init":
        others = [str(name) for name in event.get("tools") or []
                  if not str(name).startswith(TOOL_PREFIX) and name not in MCP_RESOURCE_TOOLS]
        return ", ".join(others) or None
    if event.get("type") == "assistant":
        for block in (event.get("message") or {}).get("content") or []:
            if block.get("type") == "tool_use" and not str(block.get("name") or "").startswith(TOOL_PREFIX):
                return str(block.get("name"))
    return None
