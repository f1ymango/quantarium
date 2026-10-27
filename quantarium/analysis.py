"""Read-only performance summaries from Static, Feed Batch, and Live Feed journals."""

from __future__ import annotations

import csv
import json
import math
import sqlite3
from contextlib import closing
from datetime import date, datetime, time
from pathlib import Path

from .provider import NEW_YORK


def _connection(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    return sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)


def _finite_nonnegative(value: object, field: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"Invalid {field} in saved event")
    return number


def _cell_curve(
    initial_cash: float,
    valuations: list[tuple[str, str]],
    orders: list[str],
    *, minute: bool = False,
) -> tuple[dict, list[dict]]:
    if not math.isfinite(initial_cash) or initial_cash <= 0:
        raise ValueError("Saved cell has invalid initial cash")
    peak = initial_cash
    previous = initial_cash
    worst_drawdown = 0.0
    worst_date: str | None = None
    last_date: str | None = None
    time_key = "timestamp" if minute else "date"
    return_key = "minute_return_pct" if minute else "daily_return_pct"
    curve = []
    returns: list[float] = []
    exposures: list[float] = []
    for day, payload_json in valuations:
        if minute:
            stamp = datetime.fromisoformat(day)
            if stamp.tzinfo is None:
                raise ValueError("Live valuation timestamp lacks timezone")
        else:
            date.fromisoformat(day)
        if last_date is not None and day <= last_date:
            raise ValueError("Valuation dates are not strictly increasing")
        payload = json.loads(payload_json)
        equity = _finite_nonnegative(payload["equity"], "equity")
        if previous:
            returns.append(equity / previous - 1)
        if equity:
            idle = float(payload.get("cash", equity)) + sum(payload.get("receivables", {}).values())
            exposures.append(100 * (equity - idle) / equity)
        peak = max(peak, equity)
        drawdown = 100 * (1 - equity / peak)
        if drawdown > worst_drawdown:
            worst_drawdown = drawdown
            worst_date = day
        curve.append({
            time_key: day,
            "equity": equity,
            "index_100": 100 * equity / initial_cash,
            return_key: 100 * (equity / previous - 1) if previous else None,
            "drawdown_pct": drawdown,
        })
        previous = equity
        last_date = day
    traded_notional = 0.0
    commission_total = 0.0
    filled_orders = 0
    for payload_json in orders:
        payload = json.loads(payload_json)
        shares = _finite_nonnegative(payload["filled_shares"], "filled shares")
        commission = _finite_nonnegative(payload["commission"], "commission")
        commission_total += commission
        if shares:
            price = _finite_nonnegative(payload["fill_price"], "fill price")
            if price <= 0:
                raise ValueError("Filled order has nonpositive price")
            traded_notional += shares * price
            filled_orders += 1
    # Annualized with 252 sessions a year, and 390 minutes a session for Live.
    periods_per_year = 252 * 390 if minute else 252
    volatility = sharpe = None
    if len(returns) >= 2:
        mean = sum(returns) / len(returns)
        deviation = math.sqrt(sum((value - mean) ** 2 for value in returns) / (len(returns) - 1))
        volatility = 100 * deviation * math.sqrt(periods_per_year)
        if deviation > 0:
            sharpe = mean / deviation * math.sqrt(periods_per_year)
    average_equity = sum(point["equity"] for point in curve) / len(curve) if curve else 0
    summary = {
        ("minutes" if minute else "sessions"): len(curve),
        ("last_timestamp" if minute else "last_date"): last_date,
        "total_return_pct": 100 * (previous / initial_cash - 1) if curve else None,
        "max_drawdown_pct": worst_drawdown if curve else None,
        ("max_drawdown_timestamp" if minute else "max_drawdown_date"): worst_date,
        "orders_submitted": len(orders),
        "orders_filled": filled_orders,
        "traded_notional": traded_notional,
        "commission_total": commission_total,
        "annualized_volatility_pct": volatility,
        # Mean over standard deviation of period returns, annualized, with a
        # zero risk-free rate.
        "sharpe_ratio_rf0": sharpe,
        # Buys plus sells over average equity.
        "turnover_ratio": traded_notional / average_equity if average_equity else None,
        "average_exposure_pct": sum(exposures) / len(exposures) if exposures else None,
    }
    return summary, curve


def _forward_split(curve: list[dict], created_utc: str | None, initial_cash: float) -> dict:
    """Label Feed sessions as backfill or forward relative to the frozen config.

    A session is forward only if its regular close (16:00 New York; early
    closes are not distinguished) came after the cell's configuration was
    saved. Forward return is measured from the last backfill valuation, or
    from initial cash when there was no backfill.
    """
    if created_utc is None:
        return {}
    created = datetime.fromisoformat(created_utc)
    forward_count = 0
    base: float | None = None
    for point in curve:
        close = datetime.combine(date.fromisoformat(point["date"]), time(16), NEW_YORK)
        point["phase"] = "forward" if close > created else "backfill"
        if point["phase"] == "forward":
            forward_count += 1
        else:
            base = point["equity"]
    last = curve[-1]["equity"] if curve else None
    start = base if base is not None else initial_cash
    return {
        "config_frozen_utc": created_utc,
        "backfill_sessions": len(curve) - forward_count,
        "forward_sessions": forward_count,
        "forward_return_pct": (100 * (last / start - 1)
                               if forward_count and last is not None and start else None),
    }


def read_analysis(
    path: Path,
    kind: str,
    experiment_id: str | None = None,
    *,
    benchmark: str | None = None,
) -> dict:
    """Read one saved experiment and compare cell equity on common dates."""
    if kind not in {"static", "feed", "live"}:
        raise ValueError("kind must be static, feed, or live")
    with closing(_connection(path)) as connection:
        cell_statuses: dict[str, tuple[str, str | None]] = {}
        if kind == "static":
            if experiment_id:
                metadata = connection.execute(
                    "SELECT id, status FROM runs WHERE id=?", (experiment_id,)
                ).fetchone()
            else:
                metadata = connection.execute(
                    "SELECT id, status FROM runs WHERE status='completed' "
                    "ORDER BY created_utc DESC LIMIT 1"
                ).fetchone()
            if metadata is None:
                raise ValueError("Static run not found")
            experiment_id, status = metadata
            if status != "completed":
                raise ValueError(f"Static run is {status}; analyze a completed run")
            cells = connection.execute(
                "SELECT name, mode, initial_cash FROM cells WHERE run_id=? ORDER BY name",
                (experiment_id,),
            ).fetchall()
            events_table = "events"
            id_column = "run_id"
        elif kind == "feed":
            if not experiment_id:
                raise ValueError("Feed analysis requires a feed_id")
            metadata = connection.execute(
                "SELECT id FROM feed_experiments WHERE id=?", (experiment_id,)
            ).fetchone()
            if metadata is None:
                raise ValueError("Feed experiment not found")
            cells = connection.execute(
                "SELECT name, mode, initial_cash FROM feed_cells WHERE feed_id=? ORDER BY name",
                (experiment_id,),
            ).fetchall()
            events_table = "feed_events"
            id_column = "feed_id"
            status = "active"
        else:
            if not experiment_id:
                raise ValueError("Live analysis requires a feed_id")
            metadata = connection.execute(
                "SELECT status FROM live_experiments WHERE id=?", (experiment_id,)
            ).fetchone()
            if metadata is None:
                raise ValueError("Live Feed experiment not found")
            status = metadata[0]
            cells = connection.execute(
                "SELECT name, mode, initial_cash FROM live_cells "
                "WHERE feed_id=? ORDER BY name", (experiment_id,),
            ).fetchall()
            events_table = "live_events"
            id_column = "feed_id"
        cell_table, cell_key = {"static": ("cells", "run_id"), "feed": ("feed_cells", "feed_id"),
                                "live": ("live_cells", "feed_id")}[kind]
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({cell_table})")}
        cell_created: dict[str, str | None] = {}
        scenario_feed = kind == "feed" and connection.execute(
            "SELECT provider FROM feed_experiments WHERE id=?", (experiment_id,),
        ).fetchone()[0] == "scenario"
        if kind == "feed":
            experiment_created = connection.execute(
                "SELECT created_utc FROM feed_experiments WHERE id=?", (experiment_id,),
            ).fetchone()[0]
            created = "created_utc" if "created_utc" in columns else "NULL"
            cell_created = {
                name: value or experiment_created
                for name, value in connection.execute(
                    f"SELECT name, {created} FROM feed_cells WHERE feed_id=?", (experiment_id,),
                )
            }
        if "status" in columns:
            cell_statuses = {
                name: (cell_status, error)
                for name, cell_status, error in connection.execute(
                    f"SELECT name, status, error FROM {cell_table} WHERE {cell_key}=?",
                    (experiment_id,),
                )
            }
        time_column = "timestamp" if kind == "live" else "date"
        if not cells:
            raise ValueError("Experiment has no cells")
        cell_summaries = []
        curves: dict[str, list[dict]] = {}
        for name, mode, initial_cash in cells:
            valuations = connection.execute(
                f"SELECT {time_column}, payload_json FROM {events_table} "
                f"WHERE {id_column}=? AND cell_name=? AND kind='valuation' ORDER BY sequence",
                (experiment_id, name),
            ).fetchall()
            orders = [row[0] for row in connection.execute(
                f"SELECT payload_json FROM {events_table} "
                f"WHERE {id_column}=? AND cell_name=? AND kind='order' ORDER BY sequence",
                (experiment_id, name),
            )]
            summary, curve = _cell_curve(initial_cash, valuations, orders,
                                         minute=kind == "live")
            default = "completed" if kind == "static" else "active"
            cell_status, cell_error = cell_statuses.get(name, (default, None))
            if kind == "live" and cell_status == "active" and status in {"complete", "halted"}:
                cell_status = "completed" if status == "complete" else "stopped"
            if kind == "feed" and scenario_feed:
                for point in curve:
                    point["phase"] = "scenario"
                summary.update({"backfill_sessions": 0, "forward_sessions": 0,
                                "forward_return_pct": None, "input": "scenario"})
            elif kind == "feed":
                summary.update(_forward_split(curve, cell_created.get(name), initial_cash))
            cell_summaries.append({
                "name": name, "mode": mode, "initial_cash": initial_cash, **summary,
                "status": cell_status, "error": cell_error,
            })
            curves[name] = curve
    names = {cell["name"] for cell in cell_summaries}
    if benchmark is not None and benchmark not in names:
        raise ValueError(f"Benchmark cell not found: {benchmark}")
    # Cells can start late (added to a Feed experiment) or stop early (failed or
    # retired), so comparisons use only the times both curves cover.
    benchmark_curve = curves[benchmark] if benchmark is not None else None
    if benchmark_curve is not None:
        benchmark_return = next(
            cell["total_return_pct"] for cell in cell_summaries if cell["name"] == benchmark
        )
        for cell in cell_summaries:
            value = cell["total_return_pct"]
            curve = curves[cell["name"]]
            same_span = bool(curve) and bool(benchmark_curve) and (
                curve[0][time_column] == benchmark_curve[0][time_column]
                and curve[-1][time_column] == benchmark_curve[-1][time_column]
            )
            cell["excess_return_pp"] = (
                value - benchmark_return if value is not None and same_span else None
            )
    benchmark_by_time = ({point[time_column]: point for point in benchmark_curve}
                         if benchmark_curve is not None else {})
    rows = []
    for cell in cell_summaries:
        curve = curves[cell["name"]]
        # A cell that joined late is compared with the benchmark rebased to
        # this cell's index on its first valuation.
        start = benchmark_by_time.get(curve[0][time_column]) if curve else None
        late = bool(benchmark_curve) and bool(curve) and (
            curve[0][time_column] != benchmark_curve[0][time_column]
        )
        for point in curve:
            row = {"cell": cell["name"], **point}
            if benchmark_curve is not None:
                counterpart = benchmark_by_time.get(point[time_column])
                if counterpart is None or (late and start is None):
                    benchmark_index = None
                elif late:
                    benchmark_index = (curve[0]["index_100"] * counterpart["equity"]
                                       / start["equity"])
                else:
                    benchmark_index = counterpart["index_100"]
                row["benchmark_index_100"] = benchmark_index
                row["excess_return_pp"] = (
                    point["index_100"] - benchmark_index
                    if benchmark_index is not None else None
                )
            rows.append(row)
    return {
        "kind": kind,
        "experiment_id": experiment_id,
        "status": status if rows or kind == "live" else "waiting_for_data",
        "benchmark": benchmark,
        "cells": cell_summaries,
        "curve": rows,
    }


def write_curve_csv(path: Path, analysis: dict) -> None:
    """Export a tidy per-cell curve for plotting in a spreadsheet or notebook."""
    time_key = "timestamp" if analysis["kind"] == "live" else "date"
    return_key = "minute_return_pct" if analysis["kind"] == "live" else "daily_return_pct"
    columns = [time_key, "cell", "equity", "index_100", return_key, "drawdown_pct"]
    if analysis["benchmark"] is not None:
        columns.extend(("benchmark_index_100", "excess_return_pp"))
    if analysis["kind"] == "feed":
        columns.append("phase")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        for row in sorted(analysis["curve"], key=lambda item: (item[time_key], item["cell"])):
            writer.writerow({column: row.get(column) for column in columns})
