"""Append-only record of every agent request, in ``.quantarium/agent-log.sqlite``.

Each request is logged once, whether it succeeded, was refused, or failed,
with its parameters redacted. Budgets are counted from this log, so a
request that was refused never counts against one.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOG_FILE = ".quantarium/agent-log.sqlite"
MAX_PARAMS = 4000
SECRET_KEY = re.compile(r"key|token|secret|password|credential|authorization", re.IGNORECASE)
SECRET_VALUE = re.compile(r"\b((?:qtm|blm)_[\w-]{8,}|sk-[\w-]{8,}|sk-ant-[\w-]{8,}|AKIA[0-9A-Z]{12,})")


def redact(value: object) -> object:
    """Replace secrets by key name or by their known shapes."""
    if isinstance(value, dict):
        return {key: "[redacted]" if SECRET_KEY.search(str(key)) else redact(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return SECRET_VALUE.sub("[redacted]", value)
    return value


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AgentLog:
    def __init__(self, workspace) -> None:
        self.path: Path = workspace.root / LOG_FILE
        self._lock = threading.Lock()

    @contextmanager
    def _db(self):
        """One connection per call, committed and always closed."""
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("""CREATE TABLE IF NOT EXISTS agent_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time_utc TEXT NOT NULL,
            connection_id TEXT,
            connection_name TEXT,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            params_json TEXT NOT NULL,
            outcome TEXT NOT NULL,
            status INTEGER NOT NULL,
            code TEXT,
            message TEXT,
            job_id TEXT,
            duration_ms INTEGER NOT NULL)""")
        connection.execute("CREATE INDEX IF NOT EXISTS agent_calls_connection "
                           "ON agent_calls(connection_id, time_utc)")
        # Each cell variant (by settings fingerprint) a connection has simulated.
        connection.execute("""CREATE TABLE IF NOT EXISTS agent_variants (
            connection_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            cell_id TEXT NOT NULL,
            first_utc TEXT NOT NULL,
            PRIMARY KEY (connection_id, fingerprint))""")
        return connection

    def record(self, *, connection: dict | None, kind: str, name: str, params: dict,
               outcome: str, status: int, code: str | None = None, message: str | None = None,
               job_id: str | None = None, duration_ms: int = 0) -> None:
        params_json = json.dumps(redact(params), default=str)
        if len(params_json) > MAX_PARAMS:
            params_json = json.dumps({"truncated": params_json[:MAX_PARAMS]})
        with self._lock, self._db() as db:
            db.execute(
                "INSERT INTO agent_calls(time_utc, connection_id, connection_name, kind, name, "
                "params_json, outcome, status, code, message, job_id, duration_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (_now().isoformat(), (connection or {}).get("id"), (connection or {}).get("name"),
                 kind, name, params_json, outcome, status, code,
                 SECRET_VALUE.sub("[redacted]", message)[:500] if message else None,
                 job_id, duration_ms))

    def entries(self, connection_id: str | None = None, limit: int = 100) -> list[dict]:
        if not self.path.is_file():
            return []
        limit = max(1, min(int(limit), 1000))
        query = "SELECT * FROM agent_calls"
        values: tuple = ()
        if connection_id is not None:
            query += " WHERE connection_id = ?"
            values = (connection_id,)
        with self._lock, self._db() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(query + " ORDER BY id DESC LIMIT ?", values + (limit,)).fetchall()
        return [{**dict(row), "params": json.loads(row["params_json"])} for row in rows]

    def record_variants(self, connection_id: str, fingerprints: dict[str, str]) -> None:
        with self._lock, self._db() as db:
            for cell_id, fingerprint in fingerprints.items():
                db.execute("INSERT OR IGNORE INTO agent_variants VALUES (?, ?, ?, ?)",
                           (connection_id, fingerprint, cell_id, _now().isoformat()))

    def variants(self, connection_id: str) -> int:
        """How many distinct cell variants a connection has simulated."""
        if not self.path.is_file():
            return 0
        with self._lock, self._db() as db:
            return db.execute("SELECT COUNT(*) FROM agent_variants WHERE connection_id = ?",
                              (connection_id,)).fetchone()[0]

    def count(self, connection_id: str, names: tuple[str, ...], kind: str, since: timedelta) -> int:
        """Successful calls of these operations by one connection within a window."""
        if not self.path.is_file():
            return 0
        start = (_now() - since).isoformat()
        marks = ",".join("?" for _ in names)
        with self._lock, self._db() as db:
            return db.execute(
                f"SELECT COUNT(*) FROM agent_calls WHERE connection_id = ? AND kind = ? "
                f"AND outcome = 'ok' AND time_utc >= ? AND name IN ({marks})",
                (connection_id, kind, start, *names)).fetchone()[0]
