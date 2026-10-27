"""SQLite run metadata and append-only cell events."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .model import CellSpec

# The longest a Static run's journal holds events before committing them.
COMMIT_SECONDS = 1.0


class Journal:
    def __init__(self, path: Path, config_json: str, data_sha256: str,
                 metadata: dict | None = None):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                created_utc TEXT NOT NULL,
                finished_utc TEXT,
                status TEXT NOT NULL,
                error TEXT,
                config_json TEXT NOT NULL,
                data_sha256 TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cells (
                run_id TEXT NOT NULL REFERENCES runs(id),
                name TEXT NOT NULL,
                mode TEXT NOT NULL,
                symbols_json TEXT NOT NULL,
                initial_cash REAL NOT NULL,
                final_equity REAL,
                PRIMARY KEY (run_id, name)
            );
            CREATE TABLE IF NOT EXISTS events (
                run_id TEXT NOT NULL,
                cell_name TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                date TEXT NOT NULL,
                kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (run_id, cell_name, sequence),
                FOREIGN KEY (run_id, cell_name) REFERENCES cells(run_id, name)
            );
            CREATE INDEX IF NOT EXISTS events_kind_date ON events(run_id, kind, date);
            """
        )
        run_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(runs)")}
        if "metadata_json" not in run_columns:
            # Free-form labels such as the suite and scenario a run belongs to.
            self.connection.execute("ALTER TABLE runs ADD COLUMN metadata_json TEXT")
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(cells)")}
        if "status" not in columns:
            # Cells saved before per-cell failure isolation either all finished
            # or the whole run failed.
            self.connection.execute(
                "ALTER TABLE cells ADD COLUMN status TEXT NOT NULL DEFAULT 'completed'"
            )
        if "error" not in columns:
            self.connection.execute("ALTER TABLE cells ADD COLUMN error TEXT")
        if "information_end" not in columns:
            self.connection.execute("ALTER TABLE cells ADD COLUMN information_end TEXT")
        self.run_id = uuid4().hex
        self._sequences: dict[str, int] = {}
        self._committed: dict[str, int] = {}
        self._savepoint = False
        self._last_commit = time.monotonic()
        self.connection.execute(
            "INSERT INTO runs(id, created_utc, status, config_json, data_sha256, metadata_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (self.run_id, datetime.now(timezone.utc).isoformat(), "running", config_json,
             data_sha256, json.dumps(metadata, sort_keys=True) if metadata else None),
        )
        self.connection.commit()

    def add_cell(self, cell: CellSpec) -> None:
        self._sequences[cell.name] = 0
        self.connection.execute(
            "INSERT INTO cells(run_id, name, mode, symbols_json, initial_cash, status, information_end) "
            "VALUES (?, ?, ?, ?, ?, 'active', ?)",
            (self.run_id, cell.name, cell.mode, json.dumps(cell.symbols), cell.initial_cash,
             cell.information_end.isoformat() if cell.information_end else None),
        )
        self.checkpoint(durable=True)

    def event(self, cell_name: str, day: str, kind: str, payload: object) -> None:
        self._sequences[cell_name] += 1
        self.connection.execute(
            "INSERT INTO events(run_id, cell_name, sequence, date, kind, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
            (self.run_id, cell_name, self._sequences[cell_name], day, kind, json.dumps(payload, sort_keys=True)),
        )

    def checkpoint(self, durable: bool = False) -> None:
        """Mark a point that ``rollback`` returns to.

        Committing makes SQLite wait for the disk, which cost nearly half of a
        long daily replay, so between durable points a savepoint marks the
        place and the transaction commits at most once a second.
        """
        now = time.monotonic()
        if durable or not self.connection.in_transaction or now - self._last_commit >= COMMIT_SECONDS:
            self.connection.commit()
            self._savepoint = False
            self._last_commit = now
        else:
            if self._savepoint:
                self.connection.execute("RELEASE checkpoint")
            self.connection.execute("SAVEPOINT checkpoint")
            self._savepoint = True
        self._committed = dict(self._sequences)

    def rollback(self) -> None:
        """Discard events written since the last checkpoint."""
        if self._savepoint:
            self.connection.execute("ROLLBACK TO checkpoint")
        else:
            self.connection.rollback()
        self._sequences = dict(self._committed)

    def finish_cell(self, name: str, final_equity: float) -> None:
        self.connection.execute(
            "UPDATE cells SET final_equity = ?, status = 'completed' WHERE run_id = ? AND name = ?",
            (final_equity, self.run_id, name),
        )
        self.checkpoint(durable=True)

    def fail_cell(self, name: str, day: str, reason: str, final_equity: float) -> None:
        """Stop one cell at its last committed day; other cells keep running."""
        self.event(name, day, "cell_failed", {"reason": reason})
        self.connection.execute(
            "UPDATE cells SET final_equity = ?, status = 'failed', error = ? "
            "WHERE run_id = ? AND name = ?",
            (final_equity, reason, self.run_id, name),
        )
        self.checkpoint(durable=True)

    def finish(self, status: str, error: str | None = None) -> None:
        self.connection.execute(
            "UPDATE runs SET finished_utc = ?, status = ?, error = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), status, error, self.run_id),
        )
        self.connection.commit()
        self.connection.close()


def read_report(path: Path, run_id: str | None = None) -> dict:
    connection = sqlite3.connect(path)
    try:
        connection.row_factory = sqlite3.Row
        if run_id is None:
            row = connection.execute("SELECT * FROM runs ORDER BY created_utc DESC LIMIT 1").fetchone()
        else:
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise ValueError("Run not found")
        columns = {column[1] for column in connection.execute("PRAGMA table_info(cells)")}
        status = "status" if "status" in columns else "'completed' AS status"
        error = "error" if "error" in columns else "NULL AS error"
        information_end = "information_end" if "information_end" in columns else "NULL AS information_end"
        cells = connection.execute(
            f"SELECT name, mode, symbols_json, initial_cash, final_equity, {status}, {error}, {information_end} "
            "FROM cells WHERE run_id = ? ORDER BY name",
            (row["id"],),
        ).fetchall()
        return {
            "run_id": row["id"],
            "status": row["status"],
            "error": row["error"],
            "data_sha256": row["data_sha256"],
            "cells": [
                {
                    "name": cell["name"],
                    "mode": cell["mode"],
                    "symbols": json.loads(cell["symbols_json"]),
                    "initial_cash": cell["initial_cash"],
                    "final_equity": cell["final_equity"],
                    "status": cell["status"],
                    "error": cell["error"],
                    "information_end": cell["information_end"],
                    "information_end_source": "cell_config_unverified" if cell["information_end"] else None,
                    "return_pct": (
                        100 * (cell["final_equity"] / cell["initial_cash"] - 1)
                        if cell["final_equity"] is not None else None
                    ),
                }
                for cell in cells
            ],
        }
    finally:
        connection.close()

