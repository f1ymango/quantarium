"""Compare saved Feed and Static event trails without rerunning a strategy."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from itertools import zip_longest
from pathlib import Path


def _open_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    return sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)


def _events(connection: sqlite3.Connection, table: str, id_column: str,
            experiment_id: str, cell: str):
    for day, kind, body in connection.execute(
        f"SELECT date, kind, payload_json FROM {table} "
        f"WHERE {id_column}=? AND cell_name=? ORDER BY sequence",
        (experiment_id, cell),
    ):
        payload = json.loads(body)
        if kind == "signal":
            # Feed does not know if another session will arrive; Static does.
            payload.pop("execute_on_next_session", None)
        yield {"date": day, "kind": kind, "payload": payload}


def audit_replay(
    feed_database: Path, feed_id: str, static_database: Path,
    run_id: str | None = None,
) -> dict:
    """Report differences between a Feed journal and a completed Static replay.

    Both runs must use the same cell configuration, strategy source, raw bars,
    actions, and transaction-cost settings for a match.
    """
    with closing(_open_readonly(feed_database)) as feed, closing(_open_readonly(static_database)) as static:
        if feed.execute("SELECT 1 FROM feed_experiments WHERE id=?", (feed_id,)).fetchone() is None:
            raise ValueError(f"Feed experiment not found: {feed_id}")
        if run_id is None:
            row = static.execute(
                "SELECT id FROM runs WHERE status='completed' ORDER BY created_utc DESC LIMIT 1"
            ).fetchone()
        else:
            row = static.execute("SELECT id, status FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is not None and row[1] != "completed":
                raise ValueError(f"Static run is not completed: {run_id}")
        if row is None:
            raise ValueError("Completed Static run not found")
        selected_run = row[0]
        feed_cells = {
            name: (mode, sorted(json.loads(state)["positions"]), cash)
            for name, mode, state, cash in feed.execute(
                "SELECT name, mode, state_json, initial_cash FROM feed_cells WHERE feed_id=?",
                (feed_id,),
            )
        }
        static_cells = {
            name: (mode, sorted(json.loads(symbols)), cash)
            for name, mode, symbols, cash in static.execute(
                "SELECT name, mode, symbols_json, initial_cash FROM cells WHERE run_id=?",
                (selected_run,),
            )
        }
        mismatches: list[dict] = []
        compared = 0
        for cell in sorted(set(feed_cells) | set(static_cells)):
            if feed_cells.get(cell) != static_cells.get(cell):
                mismatches.append({"cell": cell, "reason": "cell configuration differs or is missing",
                                   "feed": feed_cells.get(cell), "static": static_cells.get(cell)})
                continue
            feed_events = _events(feed, "feed_events", "feed_id", feed_id, cell)
            static_events = _events(static, "events", "run_id", selected_run, cell)
            for index, (left, right) in enumerate(zip_longest(feed_events, static_events), 1):
                compared += 1
                if left != right and len(mismatches) < 20:
                    mismatches.append({"cell": cell, "event_number": index,
                                       "feed": left, "static": right})
        if compared == 0 and not mismatches:
            raise ValueError("No events available to audit")
        return {
            "feed_id": feed_id, "static_run_id": selected_run,
            "cells": sorted(set(feed_cells) | set(static_cells)),
            "events_compared": compared, "matched": not mismatches,
            "mismatches": mismatches,
        }
