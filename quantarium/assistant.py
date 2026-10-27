"""The built-in assistant: chats with the user's own model inside the app.

A chat belongs to one assistant connection (ADR-0014). Each turn sends the
conversation and the tools that connection's grant allows to the model; the
model's tool calls go through ``Api.agent_call``, the same checks, budgets,
and audit log as an external agent, using the same tool catalog as
``quantarium mcp``. The model never sees the browser token, so it cannot approve
proposals or change grants. Conversations are stored in ``.quantarium/chats/``.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import traceback
from datetime import datetime, timezone
from typing import Any, Callable

from .errors import QuantariumError, NotFound
from .mcp import INSTRUCTIONS, TOOLS, argument_problem, tier_of, tool_operation

CHATS = ".quantarium/chats"
MAX_STEPS = 25
MAX_RESULT_CHARS = 20_000
MAX_MESSAGE_CHARS = 20_000
_ID = re.compile(r"ch_[0-9a-f]{10}")

SYSTEM = INSTRUCTIONS + """

You are the assistant built into Quantarium. You work through the tools you are given, as the user's agent
connection; anything those tools refuse is outside what the user allowed. You cannot approve proposals:
the user does that in the Inbox.

When you mention something the user can open, write a reference and the app shows it:
[[cell:CELL_ID]] shows a cell's card, [[job:JOB_ID]] a job, [[proposal:PROPOSAL_ID]] a proposal,
[[scenario:KEY]] a scenario. Use ids exactly as tools return them. Write plain text; no HTML.
Be brief, and say plainly when results are thin or simulated."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Chats:
    """Conversations, one JSON file each."""

    def __init__(self, workspace) -> None:
        self.folder = workspace.root / CHATS
        self._lock = threading.RLock()

    def _path(self, chat_id: object):
        if not isinstance(chat_id, str) or not _ID.fullmatch(chat_id):
            raise NotFound(f"Unknown chat: {chat_id}", "chat_not_found")
        return self.folder / f"{chat_id}.json"

    def get(self, chat_id: object) -> dict:
        path = self._path(chat_id)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise NotFound(f"Unknown chat: {chat_id}", "chat_not_found")

    def save(self, chat: dict) -> dict:
        with self._lock:
            self.folder.mkdir(parents=True, exist_ok=True)
            chat["updated_utc"] = _now()
            path = self._path(chat["id"])
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(chat, indent=1) + "\n", encoding="utf-8")
            os.replace(temporary, path)
            return chat

    def create(self, connection: dict) -> dict:
        return self.save({"id": "ch_" + secrets.token_hex(5), "title": "New chat",
                          "connection_id": connection["id"], "connection_name": connection["name"],
                          "created_utc": _now(), "status": "idle", "error": None,
                          "usage": {"input_tokens": 0, "output_tokens": 0}, "messages": []})

    def list(self) -> list[dict]:
        if not self.folder.is_dir():
            return []
        chats = []
        for path in self.folder.glob("ch_*.json"):
            try:
                chat = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            chats.append({key: chat.get(key) for key in ("id", "title", "connection_id", "connection_name",
                                                          "created_utc", "updated_utc", "status", "usage")})
        return sorted(chats, key=lambda item: item.get("updated_utc") or "", reverse=True)

    def delete(self, chat_id: object, app_transcript: bool = False) -> dict:
        """Delete a chat and its agent app's temporary folder; optionally the app's own transcript too."""
        path = self._path(chat_id)
        from .runtimes import remove_transcripts, remove_turn_folder

        try:
            chat = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            chat = {}
        bridge = chat.get("bridge") or {}
        folder = remove_turn_folder(bridge)
        transcripts = remove_transcripts(bridge) if app_transcript else 0
        with self._lock:
            path.unlink(missing_ok=True)
        return {"deleted": chat_id, "folder_removed": folder, "app_transcripts_removed": transcripts}


class Assistant:
    def __init__(self, api, provider_for: Callable[[dict], Any]) -> None:
        self.api = api
        self.chats = Chats(api.workspace)
        self.provider_for = provider_for
        self._running: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def _connection(self, connection_id: object) -> dict:
        connection = self.api.agents.connection_by_id(connection_id)
        if connection.get("kind") not in {"direct", "bridge"}:
            raise QuantariumError("Chats use an assistant connection: a model API or an agent app",
                               "invalid_parameter")
        return connection

    def create(self, connection_id: object) -> dict:
        return self.chats.create(self._connection(connection_id))

    def tools_for(self, connection: dict) -> list[dict]:
        """The tools this connection's grant allows, so the model is not offered refusals."""
        tiers = set(connection.get("tiers") or [])
        return [{"name": name, "description": tool.description, "input_schema": tool.schema}
                for name, tool in TOOLS.items() if tier_of(tool) in tiers]

    def call_tool(self, connection: dict, name: str, arguments: object) -> tuple[bool, str]:
        """Run one tool call through the agent path; return (is_error, text for the model)."""
        tool = TOOLS.get(name)
        if tool is None:
            return True, f"Unknown tool: {name}"
        problem = argument_problem(tool, arguments)
        if problem:
            return True, problem
        kind, operation, params, key = tool_operation(name, arguments)
        try:
            result = self.api.agent_call(connection["id"], kind, operation, params, key)
            text, failed = json.dumps(result, indent=1, default=str), False
        except Exception as exc:  # the model sees the refusal or error and can recover
            code = getattr(exc, "code", None)
            body = {"error": str(exc), "code": code}
            if getattr(exc, "hint", None):
                body["hint"] = exc.hint
            text, failed = json.dumps(body), True
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + "\n[truncated; ask for less, or use a summary tool]"
        return failed, text

    def send(self, chat_id: object, text: object, page: object = None) -> dict:
        """Add the user's message and start the model's turn in the background."""
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_MESSAGE_CHARS:
            raise QuantariumError(f"A message is 1 to {MAX_MESSAGE_CHARS} characters", "invalid_parameter")
        with self._lock:
            if chat_id in self._running:
                raise QuantariumError("The assistant is still answering; wait or stop it", "chat_busy")
            chat = self.chats.get(chat_id)
            connection = self._connection(chat["connection_id"])
            provider = self.provider_for(connection)
            content = [{"type": "text", "text": text.strip()}]
            if isinstance(page, str) and page.startswith("#/") and len(page) < 300:
                content.append({"type": "text", "text": f"(The user is on the Quantarium page {page}.)"})
            chat["messages"].append({"role": "user", "content": content, "time_utc": _now()})
            if chat["title"] == "New chat":
                chat["title"] = text.strip().splitlines()[0][:60]
            chat.update(status="running", error=None)
            self.chats.save(chat)
            stop = threading.Event()
            self._running[chat["id"]] = stop
        threading.Thread(target=self._turn, args=(chat["id"], connection, provider, stop),
                         name=f"quantarium-chat-{chat['id']}", daemon=True).start()
        return chat

    def stop(self, chat_id: object) -> dict:
        with self._lock:
            event = self._running.get(chat_id)
        if event:
            event.set()
        return self.chats.get(chat_id)

    def busy(self, chat_id: str) -> bool:
        return chat_id in self._running

    def _turn(self, chat_id: str, connection: dict, provider, stop: threading.Event) -> None:
        chat = self.chats.get(chat_id)
        try:
            if hasattr(provider, "run_turn"):
                # A bridge runs the loop in the provider's own runtime.
                provider.run_turn(self.api, chat, connection, stop, self.chats.save)
                chat.update(status="idle", error=None)
                return
            tools = self.tools_for(connection)
            for _ in range(MAX_STEPS):
                if stop.is_set():
                    break
                reply = provider.complete(SYSTEM, [{"role": item["role"], "content": item["content"]}
                                                   for item in chat["messages"]], tools)
                usage = reply.get("usage") or {}
                for key in ("input_tokens", "output_tokens"):
                    chat["usage"][key] += int(usage.get(key) or 0)
                chat["messages"].append({"role": "assistant", "content": reply["content"], "time_utc": _now()})
                self.chats.save(chat)
                calls = [block for block in reply["content"] if block.get("type") == "tool_use"]
                if reply.get("stop") == "pause_turn":
                    continue  # the provider paused a long turn; resend to continue
                if not calls:
                    break
                results = []
                for block in calls:
                    if stop.is_set():
                        failed, text = True, "Stopped by the user"
                    else:
                        failed, text = self.call_tool(connection, block["name"], block.get("input") or {})
                    results.append({"type": "tool_result", "tool_use_id": block["id"], "content": text,
                                    "is_error": failed})
                chat["messages"].append({"role": "user", "content": results, "time_utc": _now()})
                self.chats.save(chat)
            else:
                chat["messages"].append({"role": "assistant", "content": [{"type": "text", "text":
                    f"I stopped after {MAX_STEPS} steps. Tell me how to continue."}], "time_utc": _now()})
            chat.update(status="idle", error=None)
        except Exception as exc:
            chat.update(status="error", error=f"{type(exc).__name__}: {exc}")
            traceback.print_exc()
        finally:
            # Save the final state before clearing the busy flag, so a reader
            # that sees the turn as finished also sees its result.
            self.chats.save(chat)
            with self._lock:
                self._running.pop(chat_id, None)


class FakeProvider:
    """A scripted model for tests: each complete() returns the next reply."""

    def __init__(self, replies: list[dict]) -> None:
        self.replies = list(replies)
        self.requests: list[dict] = []

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> dict:
        self.requests.append({"system": system, "messages": json.loads(json.dumps(messages)), "tools": tools})
        if not self.replies:
            return {"content": [{"type": "text", "text": "Done."}]}
        return self.replies.pop(0)
