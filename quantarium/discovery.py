"""Find the running Quantarium server for a workspace, or start one.

``quantarium serve`` writes ``.quantarium/server.json`` (its URL and process id)
and removes it on exit. Clients such as ``quantarium mcp`` read it so that one
server stays the only writer of a workspace.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SERVER_FILE = ".quantarium/server.json"


def pid_alive(pid: int) -> bool:
    """Whether a process is running, without signalling it (os.kill sends Ctrl+C on Windows)."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED: it exists
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def announce(root: Path, url: str) -> Path:
    path = root / SERVER_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"url": url, "pid": os.getpid(),
                                "started_utc": datetime.now(timezone.utc).isoformat()}) + "\n",
                    encoding="utf-8")
    return path


def withdraw(root: Path) -> None:
    path = root / SERVER_FILE
    try:
        if json.loads(path.read_text(encoding="utf-8")).get("pid") == os.getpid():
            path.unlink()
    except (OSError, ValueError):
        pass


def _healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/v1/health", timeout=3) as response:
            return json.loads(response.read()).get("status") == "ok"
    except (OSError, ValueError):
        return False


def running_server(root: Path) -> str | None:
    """The URL of this workspace's live server, if one is running."""
    try:
        data = json.loads((root / SERVER_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    url, pid = data.get("url"), data.get("pid")
    if isinstance(url, str) and isinstance(pid, int) and pid_alive(pid) and _healthy(url):
        return url
    return None


def ensure_server(root: Path, timeout: float = 20) -> str:
    """Return the workspace's server URL, starting a background server if none runs."""
    url = running_server(root)
    if url:
        return url
    flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    subprocess.Popen([sys.executable, "-m", "quantarium", "serve", "--workspace", str(root), "--port", "0"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=flags, start_new_session=os.name != "nt")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        url = running_server(root)
        if url:
            return url
        time.sleep(0.2)
    raise RuntimeError(f"Could not start a Quantarium server for {root}")
