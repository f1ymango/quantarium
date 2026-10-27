"""The process that runs one unreviewed strategy for the sandbox.

It reads JSON lines on stdin and answers on stdout. The first message carries
the strategy's source. The child imports the allowed modules, installs an
audit hook, runs the source, and then locks: from that point every audited
event (opening files, sockets, subprocesses, imports, exec, and so on) is
refused. The parent (``quantarium/sandbox.py``) also checks the source against
a policy first, strips the environment, limits memory, and times every call.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from types import MappingProxyType

from .model import Bar, Context

# Imported before the hook, so the strategy can use them without file access.
ALLOWED_MODULES = ("math", "statistics", "collections", "collections.abc", "itertools", "functools",
                   "dataclasses", "typing", "datetime", "random", "bisect", "heapq", "operator",
                   "fractions", "decimal")
OPTIONAL_MODULES = ("numpy",)
# Modules the allowed ones may import for themselves that strategies must not reach.
UNSAFE_MODULES = frozenset({"ctypes", "_ctypes"})
# Audited events that pure computation raises and that expose nothing.
BENIGN_EVENTS = frozenset({"builtins.id", "object.__getattr__"})

_state = {"phase": "setup"}


def _allowed_root(name: str) -> bool:
    root = name.split(".", 1)[0]
    return root in {module.split(".", 1)[0] for module in ALLOWED_MODULES + OPTIONAL_MODULES}


def _forget_unsafe_modules(modules: dict) -> None:
    """Drop ctypes from the module cache once the allowed modules are loaded.

    numpy imports ctypes for its own use. A cached module imports without an
    audited event, so a strategy's own ``import ctypes`` would succeed. With it
    dropped, that import is a real one, which the hook refuses; numpy keeps
    its own references. (Adapted from Kandodaki's ML branch, PR #2.)
    """
    for name in [name for name in modules if name in UNSAFE_MODULES or name.startswith("ctypes.")]:
        del modules[name]


def _hook(event: str, args: tuple) -> None:
    phase = _state["phase"]
    if phase == "setup":
        return
    if event in BENIGN_EVENTS:
        return
    if phase == "loading":
        # Running the strategy's own source: its imports must be allowed ones.
        if event in {"exec", "compile"}:
            return
        if event == "import" and args and isinstance(args[0], str) and _allowed_root(args[0]):
            return
    raise PermissionError(f"Not allowed in the Quantarium sandbox: {event}")


def _bar(symbol: str, row: list) -> Bar:
    return Bar(date.fromisoformat(row[0]), symbol, float(row[1]), float(row[2]), float(row[3]),
               float(row[4]), int(row[5]))


def _plain(value):
    """A JSON-safe copy of a strategy's return value or snapshot."""
    if isinstance(value, bool) or value is None or isinstance(value, (int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    try:
        return float(value)  # numpy scalars
    except (TypeError, ValueError):
        raise TypeError(f"Values must be numbers, strings, lists, or mappings, not {type(value).__name__}")


class _Discard:
    """Where anything the strategy prints goes, so it cannot reach the protocol."""

    def write(self, text: str) -> int:
        return len(text)

    def flush(self) -> None:
        pass


# The protocol's own copy of stdout, made before the strategy can run.
_OUT = None


def _send(message: dict) -> None:
    _OUT.write(json.dumps(message) + "\n")
    _OUT.flush()


def main() -> None:
    global _OUT
    _OUT = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8")
    sys.stdout = sys.__stdout__ = _Discard()
    sys.stderr = sys.__stderr__ = _Discard()
    for name in ALLOWED_MODULES:
        __import__(name)
    for name in OPTIONAL_MODULES:
        try:
            __import__(name)
        except ImportError:
            pass
    _forget_unsafe_modules(sys.modules)
    sys.addaudithook(_hook)

    strategy = None
    history: dict[str, list] = {}
    for line in sys.stdin:
        try:
            message = json.loads(line)
            op = message["op"]
            if op == "init":
                namespace: dict = {"__name__": "quantarium_sandboxed_strategy"}
                _state["phase"] = "loading"
                try:
                    exec(compile(message["source"], message.get("filename", "<strategy>"), "exec"), namespace)
                    strategy = namespace[message["class"]](**message.get("parameters", {}))
                finally:
                    _state["phase"] = "locked"
                if not callable(getattr(strategy, "on_bar", None)):
                    raise ValueError("The strategy needs an on_bar(context) method")
                stateful = callable(getattr(strategy, "snapshot", None)) and callable(getattr(strategy, "restore", None))
                _send({"ok": True, "stateful": stateful})
            elif op == "bar":
                for symbol, change in message["history"].items():
                    rows = [_bar(symbol, row) for row in change["append"]]
                    history[symbol] = rows if change.get("reset") else history.get(symbol, []) + rows
                def ask(prompt: str, schema: dict) -> dict:
                    _send({"op": "ask", "prompt": prompt, "schema": schema})
                    response = json.loads(sys.stdin.readline())
                    if response.get("op") != "ask-result" or not response.get("ok"):
                        raise ValueError(response.get("error", "Ask gateway refused the request"))
                    return response["answer"]

                context = Context(
                    date=date.fromisoformat(message["date"]),
                    bars=MappingProxyType({symbol: _bar(symbol, row) for symbol, row in message["bars"].items()}),
                    history=MappingProxyType({symbol: tuple(rows) for symbol, rows in history.items()}),
                    cash=float(message["cash"]), positions=MappingProxyType(message["positions"]),
                    equity=float(message["equity"]),
                    risk_estimates=MappingProxyType(message.get("risk_estimates", {})), ask=ask)
                _send({"ok": True, "weights": _plain(strategy.on_bar(context))})
            elif op == "snapshot":
                _send({"ok": True, "state": _plain(strategy.snapshot())})
            elif op == "restore":
                strategy.restore(message["state"])
                _send({"ok": True})
            else:
                raise ValueError(f"Unknown operation: {op}")
        except BaseException as exc:  # reported to the parent, which stops the run
            _send({"ok": False, "error": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    main()
