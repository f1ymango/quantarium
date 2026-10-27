"""Forward decision inboxes: decisions submitted before their sessions, stamped on arrival.

An inbox is an append-only JSON-lines file under ``.quantarium/inboxes``. Each
submission names the session whose close it decides for, and Quantarium stamps
the time it arrived. A decision counts only if it arrived before the next
session opened (09:30 in New York): by then no one could know that session's
prices, so the decision cannot use hindsight. A later submission for the
same date replaces an earlier one, if it too is on time; nothing is ever
rewritten. See ``docs/plans/v0.6.x-referee.md``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import threading
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Callable

from .decisions import MAX_SYMBOLS, DecisionEntry, _entry, _symbol
from .market_calendar import sessions
from .provider import NEW_YORK

FOLDER = ".quantarium/inboxes"
NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
MAX_BYTES = 10 * 1024 * 1024
OPEN = time(9, 30)
_LOCK = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def deadline(day: date) -> datetime:
    """When a decision for ``day``'s close must have arrived: the next session's open, in UTC."""
    following = min(sessions(day + timedelta(days=1), day + timedelta(days=14)))
    return datetime.combine(following, OPEN, NEW_YORK).astimezone(timezone.utc)


def inbox_path(workspace_root: Path, name: object) -> Path:
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError("An inbox name uses 1-64 letters, digits, dashes, or underscores")
    return Path(workspace_root) / FOLDER / f"{name}.jsonl"


def _append(path: Path, record: dict) -> None:
    line = json.dumps(record, sort_keys=True, allow_nan=False) + "\n"
    if path.exists() and path.stat().st_size + len(line.encode()) > MAX_BYTES:
        raise ValueError(f"An inbox is limited to {MAX_BYTES // (1024 * 1024)} MB")
    with path.open("a", encoding="utf-8") as target:
        target.write(line)
        target.flush()
        os.fsync(target.fileno())


def create(workspace_root: Path, name: object, symbols: object, created_by: str) -> dict:
    """Open a new inbox for decisions about ``symbols``."""
    path = inbox_path(workspace_root, name)
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= MAX_SYMBOLS:
        raise ValueError(f"symbols must be a list of 1 to {MAX_SYMBOLS} symbols")
    listed = sorted({_symbol(symbol) for symbol in symbols})
    with _LOCK:
        if path.exists():
            raise ValueError(f"The inbox {name} already exists")
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"kind": "inbox", "name": name, "symbols": listed, "created_utc": _now().isoformat(),
                  "created_by": created_by}
        _append(path, record)
    return summary(path)


def submit(workspace_root: Path, name: object, params: dict, submitted_by: str,
           now: Callable[[], datetime] = _now) -> dict:
    """Stamp and append one decision; return it with whether it arrived on time."""
    path = inbox_path(workspace_root, name)
    if not path.is_file():
        raise ValueError(f"No inbox is named {name}; create it first")
    item = {key: params[key] for key in ("date", "weights", "orders", "note") if key in params}
    if set(params) - {"inbox", "date", "weights", "orders", "note"}:
        raise ValueError("A decision has date, weights, orders, and note")
    if "weights" not in item:
        raise ValueError("A decision needs weights: an object of symbols to weights, or null to hold")
    day, entry = _entry(item, 0)
    with _LOCK:
        symbols = set(_header(path)["symbols"])
        named = set(entry.weights or {}) | {order.symbol for order in entry.orders or ()}
        if not named <= symbols:
            raise ValueError(f"This inbox takes decisions about {', '.join(sorted(symbols))} only")
        received = now()
        record = {"kind": "decision", "date": day.isoformat(),
                  "weights": dict(entry.weights) if entry.weights is not None else None,
                  "orders": [dataclasses.asdict(order) for order in entry.orders] if entry.orders is not None else None,
                  "note": entry.note, "received_utc": received.isoformat(), "submitted_by": submitted_by}
        _append(path, record)
    return {**record, "deadline_utc": deadline(day).isoformat(), "on_time": received < deadline(day)}


def _lines(path: Path, until_bytes: int | None = None) -> list[dict]:
    raw = path.read_bytes()
    if until_bytes is not None:
        raw = raw[:until_bytes]
    return [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]


def _header(path: Path) -> dict:
    lines = _lines(path)
    if not lines or lines[0].get("kind") != "inbox":
        raise ValueError(f"{path.name} is not a decision inbox")
    return lines[0]


@dataclasses.dataclass(frozen=True)
class InboxDecisions:
    """The on-time decisions of an inbox, read up to ``until_bytes`` when that is given."""

    symbols: tuple[str, ...]
    decisions: MappingProxyType
    late: int
    first_on_time: date | None

    def entry(self, day: date) -> DecisionEntry | None:
        return self.decisions.get(day)


def read(path: Path, until_bytes: int | None = None) -> InboxDecisions:
    from .model import Order

    lines = _lines(path, until_bytes)
    if not lines or lines[0].get("kind") != "inbox":
        raise ValueError(f"{Path(path).name} is not a decision inbox")
    decisions: dict[date, DecisionEntry] = {}
    late = 0
    for record in lines[1:]:
        day = date.fromisoformat(record["date"])
        if datetime.fromisoformat(record["received_utc"]) >= deadline(day):
            late += 1
            continue
        orders = None if record["orders"] is None else tuple(Order(**order) for order in record["orders"])
        weights = None if record["weights"] is None else MappingProxyType(record["weights"])
        decisions[day] = DecisionEntry(weights, orders, record["note"])
    return InboxDecisions(tuple(lines[0]["symbols"]), MappingProxyType(dict(sorted(decisions.items()))), late,
                          min(decisions) if decisions else None)


def pin(path: Path) -> dict:
    """The inbox as it stands: its length and hash, so a Lab registration can freeze it."""
    raw = path.read_bytes()
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def summary(path: Path) -> dict:
    header = _header(path)
    lines = _lines(path)[1:]
    current = read(path)
    return {"name": header["name"], "symbols": header["symbols"], "created_utc": header["created_utc"],
            "submissions": len(lines), "on_time": len(current.decisions), "late": current.late,
            "first_date": min(current.decisions).isoformat() if current.decisions else None,
            "last_date": max(current.decisions).isoformat() if current.decisions else None,
            "last_received_utc": lines[-1]["received_utc"] if lines else None}


def inboxes(workspace_root: Path) -> list[dict]:
    folder = Path(workspace_root) / FOLDER
    result = []
    for path in sorted(folder.glob("*.jsonl")) if folder.is_dir() else []:
        try:
            result.append(summary(path))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result.append({"name": path.stem, "error": str(exc)})
    return result
