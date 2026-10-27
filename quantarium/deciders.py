"""Deciders: outside programs that Quantarium runs session by session, showing them only the past.

The user registers a decider in the browser: a command line, the names of
environment variables it may receive (such as its own model key), and,
optionally, the training cutoff of any model behind it. Agents cannot
register, save, or run one, because a decider is the user's own program
and may spend the user's money.

Protocol: JSON lines on the program's standard input and output.

- Quantarium sends ``{"op": "init", "protocol": 1, "symbols": [...]}``; the
  decider answers ``{"ok": true}``.
- For each session Quantarium sends ``{"op": "session", "date", "bars",
  "history", "cash", "positions", "equity"}``, with ``history`` holding only
  bars it has not sent before (or ``"reset": true`` and all of them), and the
  decider answers ``{"weights": {...} or null, "orders": [...], "note": ""}``.
- Quantarium closes the decider's input when the run ends.

See ``docs/plans/v0.6.x-referee.md``.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import weakref
from datetime import date, datetime, timezone
from pathlib import Path

from . import strictjson
from .decisions import TAPE_FORMAT, _entry

SETTINGS = ".quantarium/deciders.json"
NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
MAX_ARGUMENTS = 50
MAX_ENV = 20
INIT_TIMEOUT = 60.0
SESSION_TIMEOUT = 120.0
MAX_REPLY = 1024 * 1024
# What any program needs to start, and nothing secret.
BASE_ENV = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
            "LANG", "LC_ALL", "PATHEXT", "COMSPEC")
_LOCK = threading.Lock()


class DeciderError(ValueError):
    pass


def _read(workspace_root: Path) -> list[dict]:
    path = Path(workspace_root) / SETTINGS
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("deciders", []) if isinstance(data, dict) else []


def _write(workspace_root: Path, items: list[dict]) -> None:
    path = Path(workspace_root) / SETTINGS
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"deciders": items}, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def listing(workspace_root: Path) -> list[dict]:
    return [{**item, "fingerprint": fingerprint(item)} for item in _read(workspace_root)]


def get(workspace_root: Path, name: object) -> dict:
    found = next((item for item in _read(workspace_root) if item.get("name") == name), None)
    if found is None:
        raise DeciderError(f"No decider is named {name}")
    return found


def fingerprint(item: dict) -> str:
    """What a Lab registration pins: the command, its variables, and its cutoff."""
    pinned = {key: item.get(key) for key in ("command", "env", "training_cutoff")}
    return hashlib.sha256(json.dumps(pinned, sort_keys=True).encode()).hexdigest()


def create(workspace_root: Path, params: dict) -> dict:
    name, command, env = params.get("name"), params.get("command"), params.get("env", [])
    cutoff = params.get("training_cutoff")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise DeciderError("A decider name uses 1-64 letters, digits, dashes, or underscores")
    if not isinstance(command, list) or not 1 <= len(command) <= MAX_ARGUMENTS or \
            not all(isinstance(part, str) and part and "\x00" not in part and len(part) <= 1000 for part in command):
        raise DeciderError(f"command is a list of 1 to {MAX_ARGUMENTS} arguments, the program first")
    if shutil.which(command[0]) is None and not Path(command[0]).is_file():
        raise DeciderError(f"{command[0]} was not found")
    if not isinstance(env, list) or len(env) > MAX_ENV or \
            not all(isinstance(item, str) and ENV_NAME.fullmatch(item) for item in env):
        raise DeciderError(f"env lists at most {MAX_ENV} environment variable names")
    if cutoff is not None:
        try:
            date.fromisoformat(cutoff)
        except (TypeError, ValueError):
            raise DeciderError("training_cutoff is an ISO date, or null") from None
    item = {"name": name, "command": command, "env": sorted(set(env)), "training_cutoff": cutoff,
            "created_utc": datetime.now(timezone.utc).isoformat()}
    with _LOCK:
        items = _read(workspace_root)
        if any(existing.get("name") == name for existing in items):
            raise DeciderError(f"A decider named {name} already exists")
        _write(workspace_root, items + [item])
    return {**item, "fingerprint": fingerprint(item)}


def delete(workspace_root: Path, name: object) -> dict:
    with _LOCK:
        items = _read(workspace_root)
        kept = [item for item in items if item.get("name") != name]
        if len(kept) == len(items):
            raise DeciderError(f"No decider is named {name}")
        _write(workspace_root, kept)
    return {"deleted": name}


def environment(item: dict) -> dict[str, str]:
    """The decider's environment: what programs need to start, plus only the variables the user named."""
    keep = {*BASE_ENV, *item.get("env", [])}
    return {key: value for key, value in os.environ.items() if key.upper() in keep or key in keep}


def _stop(process: subprocess.Popen, folder: str) -> None:
    for stream in (process.stdin,):
        try:
            stream.close()
        except OSError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
    except OSError:
        pass
    shutil.rmtree(folder, ignore_errors=True)


def _row(bar) -> list:
    return [bar.date.isoformat(), bar.open, bar.high, bar.low, bar.close, bar.volume]


class DeciderProcess:
    """One running decider, for one run, with its decision tape."""

    def __init__(self, item: dict, symbols: list[str], tape: Path) -> None:
        self.name = item["name"]
        self._folder = tempfile.mkdtemp(prefix="quantarium-decider-")
        options = {"creationflags": 0x08000000} if os.name == "nt" else {}
        self._process = subprocess.Popen(
            item["command"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            cwd=self._folder, env=environment(item), text=True, encoding="utf-8", bufsize=1, **options)
        self._finalizer = weakref.finalize(self, _stop, self._process, self._folder)
        self._lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._reader, daemon=True).start()
        self._sent: dict[str, tuple[int, list, list]] = {}
        self.tape = tape
        tape.parent.mkdir(parents=True, exist_ok=True)
        self._append({"format": TAPE_FORMAT, "symbols": sorted(symbols),
                      "source": {"system": f"decider {item['name']}",
                                 **({"training_cutoff": item["training_cutoff"]} if item.get("training_cutoff") else {})}})
        reply = self._call({"op": "init", "protocol": 1, "symbols": sorted(symbols)}, INIT_TIMEOUT)
        if reply.get("ok") is not True:
            raise DeciderError(f"The decider {self.name} did not start: {str(reply.get('error') or reply)[:200]}")

    def _reader(self) -> None:
        # Read at most MAX_REPLY + 1 characters at a time, so an endless line cannot fill memory.
        stream = self._process.stdout
        while line := stream.readline(MAX_REPLY + 1):
            if len(line) > MAX_REPLY:
                self._lines.put(ValueError(f"replied with more than {MAX_REPLY} characters"))
                while line and not line.endswith("\n"):
                    line = stream.readline(MAX_REPLY + 1)
                continue
            self._lines.put(line)
        self._lines.put(None)

    def _append(self, record: dict) -> None:
        with self.tape.open("a", encoding="utf-8") as target:
            target.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")

    def _call(self, message: dict, timeout: float) -> dict:
        try:
            self._process.stdin.write(json.dumps(message) + "\n")
            self._process.stdin.flush()
            line = self._lines.get(timeout=timeout)
        except queue.Empty:
            self.close()
            raise DeciderError(f"The decider {self.name} took longer than {timeout:g} seconds and was stopped")
        except OSError:
            line = None
        if line is None:
            self.close()
            raise DeciderError(f"The decider {self.name} stopped unexpectedly")
        if isinstance(line, Exception):
            self.close()
            raise DeciderError(f"The decider {self.name} {line}")
        try:
            reply = strictjson.loads(line, limit=8)
        except ValueError as exc:
            self.close()
            raise DeciderError(f"The decider {self.name} replied with something other than JSON: {exc}") from None
        if not isinstance(reply, dict):
            raise DeciderError(f"The decider {self.name} must reply with a JSON object")
        return reply

    def decide(self, context):
        """Send one session and return the decider's ``DecisionEntry``, recorded on the tape."""
        history = {}
        for symbol, bars in context.history.items():
            count = len(bars)
            sent = self._sent.get(symbol)
            if sent and 0 < sent[0] <= count and _row(bars[0]) == sent[1] and _row(bars[sent[0] - 1]) == sent[2]:
                history[symbol] = {"append": [_row(bars[index]) for index in range(sent[0], count)]}
            else:
                history[symbol] = {"reset": True, "append": [_row(bars[index]) for index in range(count)]}
            if count:
                self._sent[symbol] = (count, _row(bars[0]), _row(bars[count - 1]))
        reply = self._call({"op": "session", "date": context.date.isoformat(),
                            "bars": {symbol: _row(bar) for symbol, bar in context.bars.items()},
                            "history": history, "cash": context.cash, "positions": dict(context.positions),
                            "equity": context.equity}, SESSION_TIMEOUT)
        if set(reply) - {"weights", "orders", "note"} or "weights" not in reply:
            raise DeciderError(f"The decider {self.name} must reply with weights, and optionally orders and a note")
        try:
            day, entry = _entry({"date": context.date.isoformat(), **reply}, 0)
        except ValueError as exc:
            raise DeciderError(f"The decider {self.name} gave an invalid decision: {exc}") from None
        self._append({"date": day.isoformat(), "weights": reply["weights"], "orders": reply.get("orders"),
                      "note": entry.note})
        return entry

    def close(self) -> None:
        self._finalizer()


def tape_path(tape_folder: Path, name: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(tape_folder) / f"{name}-{stamp}-{secrets.token_hex(3)}.jsonl"
