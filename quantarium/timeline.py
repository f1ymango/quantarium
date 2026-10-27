"""Read a cell's saved daily or minute decisions, fills, and valuations."""

from __future__ import annotations

import csv
import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path


def read_timeline(
    database: Path, kind: str, cell: str, experiment_id: str | None = None,
    *, start: date | datetime | None = None,
    through: date | datetime | None = None,
) -> dict:
    """Group an existing cell's event trail without running strategies."""
    if kind not in {"static", "feed", "live"}:
        raise ValueError("kind must be static, feed, or live")
    if kind == "live":
        for label, value in (("start", start), ("through", through)):
            if value is not None and (not isinstance(value, datetime) or value.tzinfo is None):
                raise ValueError(f"Live timeline {label} needs a timezone-aware timestamp")
        start = start.astimezone(timezone.utc) if start is not None else None
        through = through.astimezone(timezone.utc) if through is not None else None
    elif isinstance(start, datetime) or isinstance(through, datetime):
        raise ValueError("Daily timeline filters must be dates")
    if start is not None and through is not None and start > through:
        raise ValueError("Timeline start must not be after through")
    if not database.is_file():
        raise FileNotFoundError(database)
    with closing(sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)) as connection:
        if kind == "static":
            if experiment_id is None:
                row = connection.execute(
                    "SELECT id, status FROM runs ORDER BY created_utc DESC LIMIT 1"
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT id, status FROM runs WHERE id=?", (experiment_id,)
                ).fetchone()
            if row is None:
                raise ValueError("Static run not found")
            experiment_id, status = row
            cell_row = connection.execute(
                "SELECT mode, initial_cash FROM cells WHERE run_id=? AND name=?",
                (experiment_id, cell),
            ).fetchone()
            table, id_column = "events", "run_id"
        elif kind == "feed":
            if experiment_id is None:
                raise ValueError("Feed timeline requires a feed_id")
            row = connection.execute(
                "SELECT id FROM feed_experiments WHERE id=?", (experiment_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Feed experiment not found")
            status = "active"
            cell_row = connection.execute(
                "SELECT mode, initial_cash FROM feed_cells WHERE feed_id=? AND name=?",
                (experiment_id, cell),
            ).fetchone()
            table, id_column = "feed_events", "feed_id"
        else:
            if experiment_id is None:
                raise ValueError("Live timeline requires a feed_id")
            row = connection.execute(
                "SELECT status FROM live_experiments WHERE id=?", (experiment_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Live Feed experiment not found")
            status = row[0]
            cell_row = connection.execute(
                "SELECT mode, initial_cash FROM live_cells WHERE feed_id=? AND name=?",
                (experiment_id, cell),
            ).fetchone()
            table, id_column = "live_events", "feed_id"
        if cell_row is None:
            raise ValueError(f"Cell not found: {cell}")
        time_column = "timestamp" if kind == "live" else "date"
        filters = [f"{id_column}=?", "cell_name=?"]
        values: list[str] = [experiment_id, cell]
        if start is not None:
            filters.append(f"{time_column}>=?")
            values.append(start.isoformat())
        if through is not None:
            filters.append(f"{time_column}<=?")
            values.append(through.isoformat())
        days: dict[str, dict] = {}
        count = 0
        for day, event_kind, payload_json in connection.execute(
            f"SELECT {time_column}, kind, payload_json FROM {table} "
            f"WHERE {' AND '.join(filters)} ORDER BY sequence", values,
        ):
            if kind == "live":
                if datetime.fromisoformat(day).tzinfo is None:
                    raise ValueError("Live event timestamp lacks timezone")
            else:
                date.fromisoformat(day)
            payload = json.loads(payload_json)
            entry = days.setdefault(day, {
                time_column: day, "valuation": None, "signal": None, "orders": [],
                "corporate_actions": [], "rebalance_skipped": None,
                "strategies": [], "other_events": [],
            })
            if event_kind == "valuation":
                entry["valuation"] = payload
            elif event_kind == "signal":
                entry["signal"] = payload
            elif event_kind == "order":
                entry["orders"].append(payload)
            elif event_kind == "corporate_action":
                entry["corporate_actions"].append(payload)
            elif event_kind == "rebalance_skipped":
                entry["rebalance_skipped"] = payload
            elif event_kind == "strategy":
                entry["strategies"].append(payload)
            else:
                entry["other_events"].append({"kind": event_kind, "payload": payload})
            count += 1
    return {
        "kind": kind, "experiment_id": experiment_id, "status": status,
        "cell": cell, "mode": cell_row[0], "initial_cash": cell_row[1],
        "event_count": count,
        ("minutes" if kind == "live" else "days"):
            [days[day] for day in sorted(days)],
    }


def write_timeline_csv(path: Path, timeline: dict) -> None:
    """Export one row per date or minute with nested decisions and orders."""
    time_column = "timestamp" if timeline["kind"] == "live" else "date"
    columns = (
        time_column, "equity", "cash", "positions_json", "receivables_json",
        "signal_json", "orders_json", "corporate_actions_json", "rebalance_skipped",
        "other_events_json",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=columns)
        writer.writeheader()
        for day in timeline["minutes" if timeline["kind"] == "live" else "days"]:
            valuation = day["valuation"] or {}
            writer.writerow({
                time_column: day[time_column], "equity": valuation.get("equity"),
                "cash": valuation.get("cash"),
                "positions_json": json.dumps(valuation.get("positions")),
                "receivables_json": json.dumps(valuation.get("receivables")),
                "signal_json": json.dumps(day["signal"]),
                "orders_json": json.dumps(day["orders"]),
                "corporate_actions_json": json.dumps(day["corporate_actions"]),
                "rebalance_skipped": day["rebalance_skipped"] is not None,
                "other_events_json": json.dumps(day["other_events"]),
            })
