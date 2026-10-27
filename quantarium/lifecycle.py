"""Retire one cell of a Feed Batch or Live Feed experiment."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

# kind -> (experiment table, cell table, event table, time column, processed-time query)
_TABLES = {
    "feed": ("feed_experiments", "feed_cells", "feed_events", "date",
             "SELECT COALESCE(MAX(b.date), e.start_date) FROM feed_experiments e "
             "LEFT JOIN feed_bars b ON b.feed_id = e.id WHERE e.id=?"),
    "live": ("live_experiments", "live_cells", "live_events", "timestamp",
             "SELECT COALESCE(MAX(b.timestamp), e.start_at) FROM live_experiments e "
             "LEFT JOIN live_minutes b ON b.feed_id = e.id WHERE e.id=?"),
}


def retire_cell(kind: str, database: Path, feed_id: str, cell: str,
                reason: str | None = None) -> dict:
    """Stop one active cell at its last processed time, keeping its history.

    The cell's account stays as it was after the last processed session or
    minute; the experiment's other cells continue. A running Live Feed process
    picks this up before its next minute. Retirement cannot be undone.
    """
    if kind not in _TABLES:
        raise ValueError("kind must be feed or live")
    if not database.is_file():
        raise FileNotFoundError(database)
    experiments, cells, events, time_column, processed = _TABLES[kind]
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA busy_timeout = 5000")
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({cells})")}
        if "status" not in columns:
            raise ValueError("Open this experiment with the current Quantarium version first")
        try:
            connection.execute("BEGIN IMMEDIATE")
            if kind == "live":
                row = connection.execute(
                    f"SELECT status FROM {experiments} WHERE id=?", (feed_id,),
                ).fetchone()
                if row is not None and row[0] != "active":
                    raise ValueError(f"Live Feed is {row[0]}; its cells can no longer change")
            row = connection.execute(
                f"SELECT status, sequence FROM {cells} WHERE feed_id=? AND name=?",
                (feed_id, cell),
            ).fetchone()
            if row is None:
                raise ValueError(f"Cell not found in {kind} experiment {feed_id}: {cell}")
            if row[0] != "active":
                raise ValueError(f"Cell {cell} is already {row[0]}")
            at = connection.execute(processed, (feed_id,)).fetchone()[0]
            sequence = row[1] + 1
            connection.execute(
                f"INSERT INTO {events}(feed_id, cell_name, sequence, {time_column}, kind, "
                "payload_json) VALUES (?, ?, ?, ?, 'cell_retired', ?)",
                (feed_id, cell, sequence, at,
                 json.dumps({"reason": reason}, sort_keys=True, separators=(",", ":"))),
            )
            connection.execute(
                f"UPDATE {cells} SET status='retired', sequence=? WHERE feed_id=? AND name=?",
                (sequence, feed_id, cell),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    return {"kind": kind, "feed_id": feed_id, "cell": cell, "status": "retired",
            "retired_at": at, "reason": reason}
