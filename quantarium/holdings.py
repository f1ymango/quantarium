"""Per-holding view of one cell's saved run, for the cell page's center view.

Prices, positions, and fills come from the cell's own journal (valuation and
order events), so this works for every mode without reloading market data.
Full series are returned only when there are few enough holdings to chart;
larger cells get one summary row per holding.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

CHART_LIMIT = 4
_TABLES = {
    "static": ("events", "run_id", "date"),
    "feed": ("feed_events", "feed_id", "date"),
    "live": ("live_events", "feed_id", "timestamp"),
}


def _thin(times: list[str], limit: int) -> list[int]:
    if len(times) <= limit:
        return list(range(len(times)))
    return sorted({round(index * (len(times) - 1) / (limit - 1)) for index in range(limit)})


def read_holdings(database: Path, kind: str, experiment_id: str | None, cell: str, *,
                  start: str | None = None, through: str | None = None,
                  series_limit: int = 400) -> dict:
    if kind not in _TABLES:
        raise ValueError("kind must be static, feed, or live")
    if not database.is_file():
        raise FileNotFoundError(database)
    table, key, time_column = _TABLES[kind]
    with closing(sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)) as db:
        if experiment_id is None:
            if kind != "static":
                raise ValueError("Feed and Live holdings need an experiment id")
            row = db.execute("SELECT id FROM runs WHERE status='completed' "
                             "ORDER BY created_utc DESC LIMIT 1").fetchone()
            if row is None:
                raise ValueError("No completed Static run")
            experiment_id = row[0]
        filters, values = [f"{key}=?", "cell_name=?"], [experiment_id, cell]
        if start:
            filters.append(f"{time_column}>=?")
            values.append(start)
        if through:
            filters.append(f"{time_column}<=?")
            values.append(through)
        rows = db.execute(
            f"SELECT {time_column}, kind, payload_json FROM {table} WHERE {' AND '.join(filters)} "
            "AND kind IN ('valuation', 'order') ORDER BY sequence", values,
        ).fetchall()
    times: list[str] = []
    prices: dict[str, list[float | None]] = {}
    shares: dict[str, list[int]] = {}
    equity: list[float] = []
    cash: list[float] = []
    fills = []
    for stamp, event, payload_json in rows:
        payload = json.loads(payload_json)
        if event == "order":
            if payload.get("filled_shares"):
                fills.append({"time": stamp, "symbol": payload["symbol"], "side": payload["side"],
                              "shares": payload["filled_shares"], "price": payload["fill_price"]})
            continue
        index = len(times)
        times.append(stamp)
        equity.append(payload["equity"])
        cash.append(payload["cash"])
        for symbol, price in payload.get("prices", {}).items():
            prices.setdefault(symbol, [None] * index).append(price)
        for symbol, count in payload.get("positions", {}).items():
            shares.setdefault(symbol, [0] * index).append(count)
        for series, fill in ((prices, None), (shares, 0)):
            for values_list in series.values():
                if len(values_list) < len(times):
                    values_list.append(fill)
    if not times:
        raise ValueError(f"No saved valuations for {cell}")
    symbols = sorted(set(prices) | set(shares))
    summary = []
    for symbol in symbols:
        series = [price for price in prices.get(symbol, []) if price is not None]
        last_price = series[-1] if series else None
        held = shares.get(symbol, [0])[-1]
        value = held * last_price if last_price is not None else 0
        summary.append({
            "symbol": symbol, "shares": held, "value": value,
            "weight_pct": 100 * value / equity[-1] if equity[-1] else None,
            "first_price": series[0] if series else None, "last_price": last_price,
            "return_pct": 100 * (series[-1] / series[0] - 1) if len(series) > 1 and series[0] else None,
        })
    result = {
        "kind": kind, "experiment_id": experiment_id, "cell": cell, "holdings": len(symbols),
        "view": "chart" if len(symbols) <= CHART_LIMIT else "tiles" if len(symbols) <= 30 else "aggregate",
        "start": times[0], "end": times[-1], "summary": summary, "fills": fills[-500:],
    }
    keep = _thin(times, series_limit)
    result["series"] = {
        "times": [times[index] for index in keep],
        "equity": [equity[index] for index in keep],
        "cash": [cash[index] for index in keep],
    }
    if len(symbols) <= CHART_LIMIT:
        result["series"]["prices"] = {symbol: [prices.get(symbol, [None] * len(times))[index]
                                               for index in keep] for symbol in symbols}
    elif len(symbols) <= 30:
        result["series"]["sparks"] = {
            symbol: [prices[symbol][index] for index in _thin(times, 30)] for symbol in prices}
    return result
