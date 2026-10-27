"""Agent runtimes: the assistant running in another app's own agent loop.

An agent app (Codex, Claude Code, or any Agent Client Protocol agent) runs
the conversation and reaches Quantarium only through ``quantarium mcp`` with a
token that is valid for one turn, so every call passes the same agent
checks as any other agent. Each profile is a class with one interface:

- ``run_turn(api, chat, connection, stop, save)`` runs one user turn,
  appending what the app does to the chat as it happens;
- ``transcripts(state)`` lists the app's own record of the chat, if any;
- ``remove_transcripts(state)`` deletes it when the user asks.

Every turn gets a new token, an empty temporary folder, and an environment
without Quantarium, model provider, or broker secrets. Quantarium never reads or
stores an app's sign-in.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..errors import QuantariumError
from ..mcp import INSTRUCTIONS

TURN_TIMEOUT = 900
FOLDER_PREFIXES = ("quantarium-turn-", "quantarium-codex-")
# Variables no agent app gets: Quantarium's own, the broker's, and model API keys.
SECRET_PREFIXES = ("QUANTARIUM_", "BARLOOM_", "APCA_", "ANTHROPIC_", "OPENAI_API", "GITHUB_", "GH_", "AWS_", "AZURE_",
                   "GOOGLE_APPLICATION_CREDENTIALS", "XAI_", "GEMINI_", "OPENROUTER_", "HF_", "HUGGING")
SECRET_SUFFIXES = ("_KEY", "_SECRET", "_PAT")
# Any of these anywhere in a variable's name marks it as a secret.
SECRET_WORDS = ("SECRET", "TOKEN", "PASSWORD", "PASSWD", "CREDENTIAL", "API_KEY", "ACCESS_KEY", "PRIVATE_KEY")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def first_turn(app: str) -> str:
    return INSTRUCTIONS + f"""

You are the assistant built into Quantarium, running through {app}. Use only the quantarium tools. You have no
shell or file access here; anything the quantarium tools refuse is outside what the user allowed. You cannot
approve proposals: the user does that in Quantarium's Inbox. When you mention a cell, job, or proposal, write
[[cell:ID]], [[job:ID]], or [[proposal:ID]] and Quantarium shows it. Be brief."""


def turn_environment(api) -> dict:
    """This process's environment without secrets, including every key a model API connection reads."""
    named = {item.get("key_env") for item in api.agents.connections() if item.get("key_env")}
    return {key: value for key, value in os.environ.items()
            if not key.upper().startswith(SECRET_PREFIXES) and not key.upper().endswith(SECRET_SUFFIXES)
            and not any(word in key.upper() for word in SECRET_WORDS) and key not in named}


def turn_folder(state: dict) -> str:
    """The chat's empty working folder for the app, made once per chat."""
    folder = state.get("folder")
    if not folder or not os.path.isdir(folder):
        folder = state["folder"] = tempfile.mkdtemp(prefix=FOLDER_PREFIXES[0])
    return folder


def mcp_server(api) -> tuple[str, list[str]]:
    """How an app starts Quantarium's MCP server for this workspace."""
    return sys.executable, ["-m", "quantarium", "mcp", "--workspace", str(api.workspace.root)]


def user_text(chat: dict) -> str:
    user = chat["messages"][-1]["content"]
    return "\n\n".join(block["text"] for block in user if block.get("type") == "text")


def resolve(executable: str, missing: str, hint: str) -> str:
    found = shutil.which(executable)
    if not found:
        raise QuantariumError(missing, "bridge_unavailable", hint)
    return found


def start(command: list[str], folder: str, env: dict) -> subprocess.Popen:
    return subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            cwd=folder, env=env, text=True, encoding="utf-8", errors="replace",
                            creationflags=0x08000000 if os.name == "nt" else 0)


def finish(process: subprocess.Popen | None) -> None:
    """Make sure the app has exited and its pipes are closed."""
    if process is None:
        return
    if process.poll() is None:
        try:
            process.stdin.close()
            process.wait(timeout=3)
        except Exception:
            pass
    if process.poll() is None:
        process.kill()
        process.wait(timeout=10)
    for stream in (process.stdin, process.stdout, process.stderr):
        try:
            stream.close()
        except Exception:
            pass


def watch(process: subprocess.Popen, stop: threading.Event, on_stop: Callable[[], None] | None = None,
          timeout: float = TURN_TIMEOUT) -> threading.Thread:
    """Stop the app when the user stops the turn or it runs too long."""
    def run() -> None:
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            if stop.is_set() or time.monotonic() > deadline:
                if on_stop is not None:
                    try:
                        on_stop()
                        process.wait(timeout=3)
                    except Exception:
                        pass
                if process.poll() is None:
                    process.kill()
                return
            time.sleep(0.2)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def say(chat: dict, text: str) -> None:
    chat["messages"].append({"role": "assistant", "time_utc": now(), "content": [{"type": "text", "text": text}]})


def tool_exchange(chat: dict, call_id: str, name: str, arguments: object, result: str, failed: bool) -> None:
    chat["messages"].append({"role": "assistant", "time_utc": now(), "content": [
        {"type": "tool_use", "id": call_id, "name": name, "input": arguments if isinstance(arguments, dict) else {}}]})
    chat["messages"].append({"role": "user", "time_utc": now(), "content": [
        {"type": "tool_result", "tool_use_id": call_id, "content": result[:20_000], "is_error": failed}]})


def remove_turn_folder(state: dict) -> bool:
    """Delete a chat's temporary folder, only if it is one Quantarium made."""
    folder = state.get("folder")
    if not isinstance(folder, str):
        return False
    path = Path(folder).resolve()
    if path.parent != Path(tempfile.gettempdir()).resolve() or not path.name.startswith(FOLDER_PREFIXES):
        return False
    shutil.rmtree(path, ignore_errors=True)
    return not path.exists()


PROFILES = {
    "codex-exec": ("codex_exec", "CodexBridge"),
    "claude-code": ("claude_code", "ClaudeCodeRuntime"),
    "acp": ("acp", "AcpRuntime"),
}


def _profile_class(profile: object):
    if profile not in PROFILES:
        raise QuantariumError(f"Unknown agent app profile: {profile}", "bridge_unavailable")
    module, name = PROFILES[profile]
    from importlib import import_module

    return getattr(import_module(f"{__name__}.{module}"), name)


def runtime_for(connection: dict):
    return _profile_class(connection.get("profile"))(connection)


def state_profile(state: dict) -> str | None:
    # Chats from v0.3.0 only ever ran through Codex.
    return state.get("profile") or ("codex-exec" if state.get("thread_id") else None)


def transcripts(state: dict) -> list[Path]:
    """The agent app's own transcript files of a chat, where Quantarium can find them."""
    profile = state_profile(state)
    return _profile_class(profile).transcripts(state) if profile else []


def remove_transcripts(state: dict) -> int:
    removed = 0
    for path in transcripts(state):
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed
