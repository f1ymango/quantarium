"""The ``codex-exec`` profile: the assistant through the user's own Codex CLI.

Quantarium runs the user's installed Codex (``codex exec --json``), which signs
in with the user's ChatGPT plan itself; Quantarium never sees that login. Codex
runs the agent loop and reaches Quantarium only through ``quantarium mcp`` with a
token that is valid for one turn. Its own shell, browser, computer-use,
image, app, plugin, and web-search tools are turned off, it runs read-only
in an empty temporary folder, and it ignores the user's Codex configuration
(so their other MCP servers are not attached).

One limit Quantarium cannot remove: Codex keeps its command runner
(``unified_exec``), which cannot be disabled. If Codex starts a command
anyway, Quantarium stops the turn at once and says so. Codex's own read-only
sandbox, with no approvals and no inherited environment, applies to anything
that ran before the stop.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from ..errors import QuantariumError
from . import UUID, finish, first_turn, mcp_server, now, resolve, say, start, tool_exchange, turn_environment, \
    turn_folder, user_text, watch

CODEX = "codex"
# Codex's own tools, off for Quantarium chats; Quantarium's MCP tools are the only ones left.
DISABLED_FEATURES = ("shell_tool", "unified_exec", "browser_use", "browser_use_external", "in_app_browser",
                     "computer_use", "apps", "plugins", "image_generation", "standalone_web_search",
                     "view_image")
# Not code_mode_host: in Codex 0.157 every tool call, MCP ones included, runs through it.
FIRST_TURN = first_turn("Codex")
_THREAD = UUID


def _toml(value: str) -> str:
    return json.dumps(value)  # a JSON string is a valid TOML basic string


class CodexBridge:
    def __init__(self, connection: dict) -> None:
        self.connection = connection

    def command(self, api, token: str, thread_id: str | None) -> list[str]:
        executable = resolve(CODEX, "Codex is not installed or not on PATH",
                             "Install the Codex CLI, sign in with your ChatGPT plan, then restart Quantarium")
        python, mcp_args = mcp_server(api)
        settings = [
            f"mcp_servers.quantarium.command={_toml(python)}",
            f"mcp_servers.quantarium.args=[{', '.join(_toml(item) for item in mcp_args)}]",
            f"mcp_servers.quantarium.env={{QUANTARIUM_TOKEN={_toml(token)}}}",
            # Quantarium checks every call itself, so Codex need not ask; nothing else is approved.
            'mcp_servers.quantarium.default_tools_approval_mode="approve"',
            'sandbox_mode="read-only"', 'approval_policy="never"', 'web_search="disabled"',
            'shell_environment_policy.inherit="none"',
        ]
        # Only a real thread id may follow "resume", never something that parses as a flag.
        if thread_id is not None and not _THREAD.fullmatch(str(thread_id)):
            raise QuantariumError("This chat's Codex thread id is invalid; start a new chat", "bridge_failed")
        base = [executable, "exec"] + (["resume", thread_id] if thread_id else [])
        options = ["--json", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules"]
        for setting in settings:
            options += ["-c", setting]
        for feature in DISABLED_FEATURES:
            options += ["--disable", feature]
        return base + options + ["-"]

    def run_turn(self, api, chat: dict, connection: dict, stop: threading.Event, save) -> None:
        """Run one user turn through Codex, appending what it does to the chat as it happens."""
        state = chat.setdefault("bridge", {})
        state["profile"] = "codex-exec"
        prompt = user_text(chat)
        if not state.get("thread_id"):
            prompt = FIRST_TURN + "\n\nThe user says:\n" + prompt
        token = api.agents.issue_session_token(connection["id"])
        folder = turn_folder(state)
        process = None
        try:
            process = start(self.command(api, token, state.get("thread_id")), folder, turn_environment(api))
            process.stdin.write(prompt)
            process.stdin.close()
            watch(process, stop)
            calls: dict[str, dict] = {}
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if _starts_command(event):
                    process.kill()
                    say(chat, "Quantarium stopped this turn: Codex started a command outside "
                        f"Quantarium's tools ({_command_text(event)}). Ask again; it should use the quantarium tools.")
                    save(chat)
                    stop.set()
                    break
                if self._apply(event, chat, calls):
                    save(chat)
            process.wait(timeout=30)
            if process.returncode not in (0, None) and not stop.is_set() and chat.get("status") != "error":
                detail = (process.stderr.read() or "")[-500:].strip()
                raise QuantariumError(f"Codex stopped with status {process.returncode}: {detail}", "bridge_failed")
        finally:
            finish(process)
            api.agents.end_session_token(connection["id"])

    @staticmethod
    def _apply(event: dict, chat: dict, calls: dict) -> bool:
        """Fold one Codex event into the chat; True when the chat changed."""
        kind = event.get("type")
        if kind == "thread.started" and _THREAD.fullmatch(str(event.get("thread_id") or "")):
            chat["bridge"]["thread_id"] = event["thread_id"]
            return True
        if kind == "turn.completed":
            usage = event.get("usage") or {}
            chat["usage"]["input_tokens"] += int(usage.get("input_tokens") or 0)
            chat["usage"]["output_tokens"] += int(usage.get("output_tokens") or 0)
            return True
        if kind in {"turn.failed", "error"}:
            message = (event.get("error") or {}).get("message") if kind == "turn.failed" else event.get("message")
            say(chat, f"Codex reported an error: {message or 'unknown'}")
            return True
        if kind != "item.completed":
            return False
        item = event.get("item") or {}
        item_type = item.get("type") or item.get("item_type")
        if item_type == "agent_message" and item.get("text"):
            say(chat, item["text"])
            return True
        if item_type == "mcp_tool_call":
            call_id = str(item.get("id") or len(calls))
            arguments = item.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    arguments = {"raw": arguments}
            result = item.get("result")
            text = json.dumps(result, indent=1, default=str) if result is not None else str(item.get("error") or "")
            failed = item.get("status") == "failed" or bool(item.get("error")) or bool(
                isinstance(result, dict) and result.get("isError"))
            tool_exchange(chat, call_id, item.get("tool") or "tool", arguments, text, failed)
            return True
        if item_type == "command_execution":
            # Its shell is off for Quantarium chats; say so if it still ran one.
            say(chat, f"Codex ran a command outside Quantarium's tools: {item.get('command')}")
            return True
        return False

    @staticmethod
    def transcripts(state: dict) -> list[Path]:
        return codex_transcripts(state.get("thread_id"))


def _starts_command(event: dict) -> bool:
    item = event.get("item") or {}
    return event.get("type") in {"item.started", "item.updated", "item.completed"} and \
        (item.get("type") or item.get("item_type")) == "command_execution"


def _command_text(event: dict) -> str:
    command = (event.get("item") or {}).get("command")
    return (" ".join(command) if isinstance(command, list) else str(command or "a command"))[:200]


def codex_transcripts(thread_id: object) -> list[Path]:
    """Codex's own transcript files for one thread, in its sessions folder."""
    if not isinstance(thread_id, str) or not _THREAD.fullmatch(thread_id):
        return []
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    sessions = home / "sessions"
    if not sessions.is_dir():
        return []
    return sorted(path for path in sessions.rglob(f"rollout-*-{thread_id}.jsonl") if path.is_file())
