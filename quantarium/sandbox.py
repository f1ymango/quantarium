"""Run an unreviewed strategy in a separate, locked-down process.

A ``SandboxedStrategy`` looks like any strategy to the engine: ``on_bar``,
and ``snapshot``/``restore`` when the strategy has them. Each call is sent to
a child process (``quantarium/sandbox_child.py``) and answered there. Layers:

1. Before starting anything, ``policy_problems`` checks the source: only
   allowed imports, and no names or attributes that reach the interpreter's
   internals (``open``, ``exec``, ``getattr``, ``__class__``, and so on).
2. The child starts isolated (``python -I``) in an empty temporary folder,
   with an environment that holds no secrets, and with its memory, CPU time,
   and process count limited (a job object on Windows, rlimits elsewhere).
3. Inside the child, an audit hook refuses every audited event once the
   strategy is loaded: files, sockets, subprocesses, imports, exec.
4. Every call has a timeout; a strategy that runs over is killed.

These layers stop a mistaken or manipulated agent's code. They are not a
boundary against a determined attacker who controls the code; a container
or WASM runner can replace the child behind this interface later.
"""

from __future__ import annotations

import ast
import json
import os
import queue
import shutil
import site
import subprocess
import sys
import tempfile
import threading
import weakref
from pathlib import Path

from .errors import QuantariumError
from .sandbox_child import ALLOWED_MODULES, OPTIONAL_MODULES

INIT_TIMEOUT = 30.0
CALL_TIMEOUT = 10.0
MEMORY_LIMIT = 1024 * 1024 * 1024
CPU_SECONDS = 300
ALLOWED_IMPORTS = frozenset(name.split(".", 1)[0] for name in ALLOWED_MODULES + OPTIONAL_MODULES)
BANNED_NAMES = frozenset({
    "open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars", "getattr",
    "setattr", "delattr", "input", "breakpoint", "help", "exit", "quit", "memoryview", "__builtins__",
    "__loader__", "__spec__",
})
ALLOWED_DUNDERS = frozenset({"__init__", "__name__", "__doc__"})
# Attributes that reach frames, code objects, or the interpreter without a dunder,
# read attributes by name at runtime, or read files below Python's audit hooks.
BANNED_ATTRIBUTES = frozenset({
    "gi_frame", "gi_code", "cr_frame", "cr_code", "ag_frame", "ag_code", "tb_frame", "tb_next",
    "f_globals", "f_locals", "f_builtins", "f_back", "f_code", "co_code", "co_consts",
    "format", "format_map", "attrgetter", "methodcaller",
    "fromfile", "tofile", "load", "loads", "loadtxt", "genfromtxt", "fromregex", "memmap", "save",
    "savez", "savez_compressed", "savetxt", "DataSource", "ctypes", "ctypeslib", "lib",
})


class SandboxError(QuantariumError):
    def __init__(self, message: str, code: str = "sandbox_failed") -> None:
        super().__init__(message, code, "Check the script with script-check, fix it, and run again")


def policy_problems(source: str, filename: str = "<script>") -> list[str]:
    """What keeps a script from running in the sandbox; empty when it may run."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        return [f"Syntax error on line {exc.lineno}: {exc.msg}"]
    problems = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", "?")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] not in ALLOWED_IMPORTS:
                    problems.append(f"Line {line}: import {alias.name} is not allowed")
        elif isinstance(node, ast.ImportFrom):
            if node.level or (node.module or "").split(".", 1)[0] not in ALLOWED_IMPORTS:
                problems.append(f"Line {line}: from {node.module or '.'} import is not allowed")
        elif isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            problems.append(f"Line {line}: {node.id} is not allowed")
        elif isinstance(node, ast.Attribute) and (
                (node.attr.startswith("__") and node.attr not in ALLOWED_DUNDERS) or node.attr in BANNED_ATTRIBUTES):
            problems.append(f"Line {line}: .{node.attr} is not allowed")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            problems.append(f"Line {line}: {type(node).__name__.lower()} is not allowed")
    allowed = ", ".join(sorted(ALLOWED_IMPORTS))
    return problems and problems + [f"Allowed imports: {allowed}"]


def _environment() -> dict[str, str]:
    # Only what Python itself needs to start; never keys or tokens.
    keep = ("SYSTEMROOT", "WINDIR") if os.name == "nt" else ("LANG",)
    return {key: os.environ[key] for key in keep if key in os.environ}


def _posix_limits() -> None:
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT * 2, MEMORY_LIMIT * 2))
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))


def _windows_job(process: subprocess.Popen):
    """Put the child in a job: one process, limited memory and CPU, killed with the job."""
    import ctypes
    from ctypes import wintypes

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise SandboxError("Could not create a job object for the sandbox")
    info = EXTENDED()
    info.BasicLimitInformation.LimitFlags = 0x2 | 0x8 | 0x100 | 0x2000  # time, processes, memory, kill on close
    info.BasicLimitInformation.PerProcessUserTimeLimit = CPU_SECONDS * 10_000_000
    info.BasicLimitInformation.ActiveProcessLimit = 1
    info.ProcessMemoryLimit = MEMORY_LIMIT
    if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        kernel32.CloseHandle(job)
        raise SandboxError("Could not limit the sandbox process")
    if not kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(int(process._handle))):
        kernel32.CloseHandle(job)
        raise SandboxError("Could not limit the sandbox process")
    return job, kernel32


def _row(bar) -> list:
    return [bar.date.isoformat(), bar.open, bar.high, bar.low, bar.close, bar.volume]


def _stop(process: subprocess.Popen, folder: str, job) -> None:
    try:
        process.kill()
        process.wait(timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass
    for stream in (process.stdin, process.stdout):
        try:
            stream.close()
        except OSError:
            pass
    if job is not None:
        handle, kernel32 = job
        kernel32.CloseHandle(handle)
    shutil.rmtree(folder, ignore_errors=True)


class SandboxedStrategy:
    def __init__(self, source: bytes, class_name: str, parameters: dict, filename: str) -> None:
        text = source.decode("utf-8")
        problems = policy_problems(text, filename)
        if problems:
            raise SandboxError(f"{filename} cannot run in the sandbox: " + "; ".join(problems), "sandbox_policy")
        self.filename = filename
        self._folder = tempfile.mkdtemp(prefix="quantarium-sandbox-")
        # Quantarium itself, then the environment's packages (numpy, if installed).
        # The child can import only the allowed modules, and only before it locks.
        paths = [str(Path(__file__).resolve().parents[1]), *site.getsitepackages()]
        bootstrap = (f"import sys; sys.path[:0] = {paths!r}; "
                     "from quantarium.sandbox_child import main; main()")
        options: dict = {}
        if os.name == "nt":
            options["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        else:
            options["preexec_fn"] = _posix_limits
        self._process = subprocess.Popen(
            # A venv's python.exe on Windows is a launcher that starts a second
            # process, which the one-process job limit forbids; use the real one.
            [getattr(sys, "_base_executable", None) or sys.executable, "-I", "-c", bootstrap], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, cwd=self._folder, env=_environment(), text=True,
            encoding="utf-8", bufsize=1, **options)
        job = None
        try:
            if os.name == "nt":
                job = _windows_job(self._process)
        finally:
            self._finalizer = weakref.finalize(self, _stop, self._process, self._folder, job)
        self._lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        self._sent: dict[str, tuple[int, list, list]] = {}
        reply = self._call({"op": "init", "source": text, "class": class_name,
                            "parameters": parameters, "filename": filename}, INIT_TIMEOUT)
        if reply.get("stateful"):
            self.snapshot = lambda: self._call({"op": "snapshot"})["state"]
            self.restore = lambda state: self._call({"op": "restore", "state": state})

    def _read(self) -> None:
        for line in self._process.stdout:
            self._lines.put(line)
        self._lines.put(None)

    def _call(self, message: dict, timeout: float | None = None, ask_handler=None) -> dict:
        timeout = CALL_TIMEOUT if timeout is None else timeout
        try:
            self._process.stdin.write(json.dumps(message) + "\n")
            self._process.stdin.flush()
            line = self._lines.get(timeout=timeout)
        except queue.Empty:
            self.close()
            raise SandboxError(f"{self.filename} took longer than {timeout:g} seconds and was stopped")
        except OSError:
            line = None
        if line is None:
            self.close()
            raise SandboxError(f"{self.filename} stopped unexpectedly (it may have hit a memory or time limit)")
        reply = json.loads(line)
        while reply.get("op") == "ask":
            try:
                if ask_handler is None:
                    raise ValueError("context.ask is not enabled")
                answer = ask_handler(reply.get("prompt"), reply.get("schema"))
                response = {"op": "ask-result", "ok": True, "answer": answer}
            except Exception as exc:
                response = {"op": "ask-result", "ok": False,
                            "error": f"{type(exc).__name__}: {exc}"[:300]}
            self._process.stdin.write(json.dumps(response) + "\n")
            self._process.stdin.flush()
            try:
                line = self._lines.get(timeout=timeout)
            except queue.Empty:
                self.close()
                raise SandboxError(f"{self.filename} took longer than {timeout:g} seconds and was stopped")
            if line is None:
                self.close()
                raise SandboxError(f"{self.filename} stopped unexpectedly after ask")
            reply = json.loads(line)
        if not reply.get("ok"):
            raise SandboxError(f"{self.filename} failed in the sandbox: {reply.get('error')}")
        return reply

    def on_bar(self, context):
        history = {}
        for symbol, bars in context.history.items():
            count = len(bars)
            sent = self._sent.get(symbol)
            # Send only new bars, unless a split changed the ones already sent.
            if sent and 0 < sent[0] <= count and _row(bars[0]) == sent[1] and _row(bars[sent[0] - 1]) == sent[2]:
                history[symbol] = {"append": [_row(bars[index]) for index in range(sent[0], count)]}
            else:
                history[symbol] = {"reset": True, "append": [_row(bars[index]) for index in range(count)]}
            if count:
                self._sent[symbol] = (count, _row(bars[0]), _row(bars[count - 1]))
        reply = self._call({"op": "bar", "date": context.date.isoformat(),
                            "bars": {symbol: _row(bar) for symbol, bar in context.bars.items()},
                            "history": history, "cash": context.cash,
                            "positions": dict(context.positions), "equity": context.equity,
                            "risk_estimates": dict(context.risk_estimates)},
                           ask_handler=context.ask)
        return reply["weights"]

    def close(self) -> None:
        self._finalizer()
