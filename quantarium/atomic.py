"""Replacing and reading Quantarium's own files when other threads or processes use them.

Quantarium saves a file by writing a temporary copy and moving it over the
original, so a reader sees the old file or the new one, never half of each.
On Windows the move fails while anything has the original open, and opening
the original fails while a move is under way; both raise PermissionError.
The reader may be another thread, the ``quantarium mcp`` process, or a virus
scanner. Such a clash lasts milliseconds, so these helpers retry for a few
seconds before giving up. Elsewhere PermissionError means a real permission
problem and is raised at once.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable, TypeVar

RETRY = os.name == "nt"
RETRY_SECONDS = 5.0

T = TypeVar("T")


def _retry(action: Callable[[], T]) -> T:
    if not RETRY:
        return action()
    deadline = time.monotonic() + RETRY_SECONDS
    delay = 0.001
    while True:
        try:
            return action()
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.05)


def replace(source: Path, target: Path) -> None:
    """Move ``source`` over ``target`` in one step."""
    _retry(lambda: os.replace(source, target))


def read_text(path: Path) -> str:
    return _retry(lambda: Path(path).read_text(encoding="utf-8"))


def read_bytes(path: Path) -> bytes:
    return _retry(lambda: Path(path).read_bytes())
