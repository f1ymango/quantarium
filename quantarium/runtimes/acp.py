"""The ``acp`` profile: the assistant through any Agent Client Protocol agent.

ACP is an open JSON-RPC protocol between a client and a coding agent that
runs as a subprocess on stdio (https://agentclientprotocol.com). Quantarium is
the client. For each turn it:

1. starts the agent's command in the chat's empty folder;
2. calls ``initialize``, offering no file-system or terminal capabilities,
   so the agent cannot read or write files or run commands through Quantarium;
3. calls ``session/load`` when the agent supports it and the chat has a
   session, or ``session/new`` otherwise, passing ``quantarium mcp`` with the
   turn's token as the only MCP server;
4. calls ``session/prompt`` and turns ``session/update`` events into chat
   blocks.

When the agent asks permission for a tool (``session/request_permission``),
Quantarium allows Quantarium's own tools and rejects every other. A reported tool
call that is not Quantarium's stops the turn. File and terminal requests are
refused. An ACP agent is software the user installed; what it can do on the
computer outside Quantarium is up to that app and its settings, as for any
external agent.
"""

from __future__ import annotations

import json
import queue
import re
import threading
from pathlib import Path

from .. import __version__, presets
from ..errors import QuantariumError
from ..mcp import TOOLS
from . import TURN_TIMEOUT, finish, first_turn, mcp_server, resolve, say, start, tool_exchange, turn_environment, \
    turn_folder, user_text, watch

PROTOCOL_VERSION = 1
SERVER = "quantarium"
STARTUP_TIMEOUT = 120
# Tool kinds that act on the computer itself; never Quantarium's MCP tools.
LOCAL_KINDS = {"read", "edit", "delete", "move", "search", "execute", "fetch"}
NOT_OFFERED = -32601
# What one turn may send before Quantarium stops it: enough for any answer, not for a flood.
MAX_TURN_TEXT = 200_000
MAX_TURN_CALLS = 200
RECAP_MESSAGES = 12


class AcpError(QuantariumError):
    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message, "bridge_failed", hint)


class _Connection:
    """JSON-RPC over the agent's stdio: one reader thread, requests answered in order."""

    def __init__(self, process) -> None:
        self.process = process
        self.inbox: queue.Queue = queue.Queue()
        self.next_id = 0
        self.write_lock = threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.process.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if isinstance(message, dict):
                self.inbox.put(message)
        self.inbox.put(None)

    def send(self, message: dict) -> None:
        with self.write_lock:
            try:
                self.process.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
                self.process.stdin.flush()
            except (OSError, ValueError):
                pass  # the agent has exited; the reader reports it

    def request(self, method: str, params: dict, handle, timeout: float) -> dict:
        """Send a request and handle the agent's messages until its answer arrives."""
        self.next_id += 1
        wanted = self.next_id
        self.send({"id": wanted, "method": method, "params": params})
        while True:
            try:
                message = self.inbox.get(timeout=timeout)
            except queue.Empty:
                raise AcpError(f"The agent did not answer {method} in time")
            if message is None:
                raise AcpError(f"The agent exited during {method}")
            if "method" not in message and message.get("id") == wanted:
                if "error" in message:
                    error = message["error"] if isinstance(message["error"], dict) else {}
                    raise AcpError(f"The agent refused {method}: {str(error.get('message') or error)[:300]}",
                                   "Check that it is signed in and set up; run it once in a terminal")
                return message.get("result") or {}
            if "method" in message:
                handle(message)


def quantarium_tool(tool_call: dict) -> str | None:
    """The Quantarium tool an agent's tool call is for, if it is one of Quantarium's."""
    if not isinstance(tool_call, dict) or tool_call.get("kind") in LOCAL_KINDS:
        return None
    texts = [str(tool_call.get(key) or "") for key in ("title", "name", "toolName", "toolCallId")]
    meta = tool_call.get("_meta")
    if isinstance(meta, dict):
        texts += [str(value) for value in meta.values() if isinstance(value, str)]
    for text in texts:
        # mcp__quantarium__list_cells, quantarium__list_cells, quantarium_list_cells, quantarium.list_cells, quantarium/list_cells,
        # optionally followed by a plain note in parentheses. The whole text must be the tool's name: a shell
        # command that merely mentions a Quantarium tool is not one of Quantarium's tools.
        match = re.fullmatch(r"\s*(?:mcp__)?quantarium(?:__|[_./:]| tool )([a-z][a-z0-9_]*)\s*(?:\([^;|&`$<>()]*\))?\s*",
                             text, re.IGNORECASE)
        if match and match.group(1).lower() in TOOLS:
            return match.group(1).lower()
        # list_cells (quantarium MCP Server)
        match = re.fullmatch(r"\s*([a-z][a-z0-9_]*)\s*\(\s*quantarium\b[^)]*\)\s*", text, re.IGNORECASE)
        if match and match.group(1).lower() in TOOLS:
            return match.group(1).lower()
    return None


def _count(value: object) -> int:
    """A token count an agent reported, or 0 when it is not a plain nonnegative number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not value >= 0 or value == float("inf"):
        return 0
    return int(value)


def _text(content: object) -> str:
    """The text of an ACP content block, or a list of tool-call content items."""
    if isinstance(content, dict):
        if content.get("type") == "text":
            return str(content.get("text") or "")
        if content.get("type") == "content":
            return _text(content.get("content"))
        return ""
    if isinstance(content, list):
        return "\n".join(filter(None, (_text(item) for item in content)))
    return ""


class AcpRuntime:
    def __init__(self, connection: dict) -> None:
        self.connection = connection

    def preset(self) -> dict:
        return presets.runtime_preset(self.connection.get("runtime")) or {}

    def command(self) -> list[str]:
        # A custom agent's command is on the connection; a preset's comes from the preset.
        command = list(self.connection.get("command") or self.preset().get("command") or [])
        if not command:
            raise AcpError("This connection has no agent command")
        executable = command[0]
        if not Path(executable).is_absolute():
            executable = resolve(executable, f"{executable} is not installed or not on PATH",
                                 "Install the agent app, or create the connection with its full path")
        elif not Path(executable).is_file():
            raise AcpError(f"{executable} does not exist")
        return [executable] + command[1:]

    def run_turn(self, api, chat: dict, connection: dict, stop: threading.Event, save) -> None:
        state = chat.setdefault("bridge", {})
        state["profile"] = "acp"
        folder = turn_folder(state)
        token = api.agents.issue_session_token(connection["id"])
        python, args = mcp_server(api)
        servers = [{"name": SERVER, "command": python, "args": args,
                    "env": [{"name": "QUANTARIUM_TOKEN", "value": token}]}]
        turn = _Turn(chat, save, stop)
        process = None
        try:
            # A preset may set the app's own settings for Quantarium turns, such as turning its tools off.
            process = start(self.command(), folder, {**turn_environment(api), **(self.preset().get("env") or {})})
            rpc = _Connection(process)
            session: dict = {}
            watch(process, stop, on_stop=lambda: session.get("id") and rpc.send(
                {"method": "session/cancel", "params": {"sessionId": session["id"]}}), timeout=TURN_TIMEOUT)
            replaying = _Replay(turn)
            info = rpc.request("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
                "clientInfo": {"name": "quantarium", "title": "Quantarium", "version": __version__}},
                replaying.handle(rpc), STARTUP_TIMEOUT)
            capabilities = info.get("agentCapabilities") or {}
            known = state.get("session_id")
            resumed = False
            if isinstance(known, str) and known and capabilities.get("loadSession") is True:
                try:
                    rpc.request("session/load", {"sessionId": known, "cwd": folder, "mcpServers": servers},
                                replaying.handle(rpc), STARTUP_TIMEOUT)
                    resumed = True
                except AcpError:
                    resumed = False
            if not resumed:
                created = rpc.request("session/new", {"cwd": folder, "mcpServers": servers},
                                      replaying.handle(rpc), STARTUP_TIMEOUT)
                if not isinstance(created.get("sessionId"), str) or not created["sessionId"]:
                    raise AcpError("The agent did not start a session")
                state["session_id"] = created["sessionId"]
            session["id"] = state["session_id"]
            prompt = user_text(chat)
            if not resumed:
                prompt = first_turn(self.connection.get("name") or "an agent app") + _recap(chat) \
                    + "\n\nThe user says:\n" + prompt
            result = rpc.request("session/prompt", {"sessionId": session["id"],
                                                    "prompt": [{"type": "text", "text": prompt}]},
                                 turn.handle(rpc), TURN_TIMEOUT)
            turn.flush()
            usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
            chat["usage"]["input_tokens"] += _count(usage.get("inputTokens") or usage.get("input_tokens"))
            chat["usage"]["output_tokens"] += _count(usage.get("outputTokens") or usage.get("output_tokens"))
            if result.get("stopReason") == "refusal":
                say(chat, "The agent declined to continue this request.")
            save(chat)
        except AcpError:
            turn.flush()
            if stop.is_set() or turn.stopped:
                return
            raise
        finally:
            finish(process)
            api.agents.end_session_token(connection["id"])

    @staticmethod
    def transcripts(state: dict) -> list[Path]:
        return []  # each ACP agent keeps its record of a chat in its own place


def _recap(chat: dict) -> str:
    """Earlier messages, for an agent that cannot load its previous session."""
    earlier = []
    for message in chat["messages"][:-1][-RECAP_MESSAGES:]:
        text = " ".join(block.get("text", "") for block in message["content"] if block.get("type") == "text").strip()
        if text:
            earlier.append(f"{message['role']}: {text[:2000]}")
    return ("\n\nThe conversation so far:\n" + "\n".join(earlier)) if earlier else ""


class _Replay:
    """While initializing or loading a session: answer requests, ignore replayed history."""

    def __init__(self, turn: "_Turn") -> None:
        self.turn = turn

    def handle(self, rpc: _Connection):
        def handle(message: dict) -> None:
            if "id" in message:
                self.turn.answer(rpc, message)
        return handle


class _Turn:
    """Folds the agent's updates during a prompt into the chat."""

    def __init__(self, chat: dict, save, stop: threading.Event) -> None:
        self.chat, self.save, self.stop = chat, save, stop
        self.text: list[str] = []
        self.received = 0
        self.calls: dict[str, dict] = {}
        self.stopped = False

    def flush(self) -> None:
        text = "".join(self.text).strip()
        self.text = []
        if text:
            say(self.chat, text)
            self.save(self.chat)

    def halt(self, rpc: _Connection, reason: str) -> None:
        self.flush()
        say(self.chat, f"Quantarium stopped this turn: {reason}. Quantarium allows only its own tools in these chats.")
        self.save(self.chat)
        self.stopped = True
        self.stop.set()
        rpc.process.kill()

    def answer(self, rpc: _Connection, message: dict) -> None:
        """Answer a request from the agent: permission for Quantarium's tools only; nothing else is offered."""
        method, params = message.get("method"), message.get("params")
        params = params if isinstance(params, dict) else {}
        if method == "session/request_permission":
            tool_call = params.get("toolCall") or {}
            options = [item for item in params.get("options") or [] if isinstance(item, dict)]
            allowed = quantarium_tool(tool_call) is not None
            wanted = ("allow_once", "allow_always") if allowed else ("reject_once", "reject_always")
            choice = next((item for kind in wanted for item in options if item.get("kind") == kind), None)
            if not allowed:
                self.flush()
                say(self.chat, f"Quantarium refused the agent's request to use {str(tool_call.get('title') or 'a tool')[:200]}.")
                self.save(self.chat)
            outcome = {"outcome": "selected", "optionId": choice["optionId"]} if choice else {"outcome": "cancelled"}
            rpc.send({"id": message["id"], "result": {"outcome": outcome}})
            return
        rpc.send({"id": message["id"], "error": {"code": NOT_OFFERED,
                                                 "message": f"Quantarium does not offer {str(method)[:100]}"}})

    def handle(self, rpc: _Connection):
        def handle(message: dict) -> None:
            if "id" in message:
                self.answer(rpc, message)
                return
            if message.get("method") != "session/update" or self.stopped:
                return
            params = message.get("params")
            update = params.get("update") if isinstance(params, dict) else None
            if not isinstance(update, dict):
                return
            kind = update.get("sessionUpdate")
            if kind == "agent_message_chunk":
                text = _text(update.get("content"))
                self.received += len(text)
                if self.received > MAX_TURN_TEXT:
                    self.halt(rpc, f"the agent sent more than {MAX_TURN_TEXT:,} characters of text in one turn")
                    return
                self.text.append(text)
            elif kind in {"tool_call", "tool_call_update"}:
                if len(self.calls) >= MAX_TURN_CALLS and str(update.get("toolCallId")) not in self.calls:
                    self.halt(rpc, f"the agent made more than {MAX_TURN_CALLS} tool calls in one turn")
                    return
                self.tool(rpc, update)
        return handle

    def tool(self, rpc: _Connection, update: dict) -> None:
        call_id = str(update.get("toolCallId") or len(self.calls))
        known = self.calls.get(call_id)
        if known is None:
            name = quantarium_tool(update)
            if name is None:
                self.halt(rpc, f"the agent used a tool outside Quantarium's ({str(update.get('title') or update.get('kind') or 'unknown')[:200]})")
                return
            known = self.calls[call_id] = {"name": name, "input": update.get("rawInput") or {}, "done": False}
        if update.get("rawInput"):
            known["input"] = update["rawInput"]
        status = update.get("status")
        if status in {"completed", "failed"} and not known["done"]:
            known["done"] = True
            self.flush()
            output = _text(update.get("content")) or json.dumps(update.get("rawOutput"), default=str)
            tool_exchange(self.chat, call_id, known["name"], known["input"], output, status == "failed")
            self.save(self.chat)
