"""The user's own trading history, as a decision file Quantarium can replay and judge.

A generic trades CSV has the columns ``date, symbol, side, shares, price``
(other columns are ignored): one row per fill, as most brokers can export.
A trade filled on a session becomes a market order decided at the close
before it, so Quantarium fills it at that session's open with the experiment's
costs. Trades in one symbol on one day are netted; an intraday round trip
cannot be modelled on daily bars and is listed instead.

What the conversion cannot know is listed with every result: deposits and
withdrawals, dividends and corporate actions in the account, shares held
before the history begins (suggested as starting holdings), and short sales.
As for every finished decision file, the Lab's verdict stays capped at
``unknown``. ``fill_gap`` compares Quantarium's fills with the real ones.
"""

from __future__ import annotations

import csv
import json
import math
import sqlite3
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from . import market_calendar
from .decisions import FORMAT, MAX_DECISIONS, MAX_SYMBOLS, parse_decisions
from .userdata import SYMBOL

COLUMNS = ("date", "symbol", "side", "shares", "price")
MAX_BYTES = 20 * 1024 * 1024
MAX_ROWS = 200_000
MAX_YEARS = 60
LIMITS = (
    "Deposits and withdrawals are not in a trades file; the cell starts with its initial cash and never adds any.",
    "Dividends, splits, and fees charged outside fills are not taken from the account; Quantarium applies its own costs.",
    "Shares held before the first trade must be given as the cell's starting holdings.",
    "Short sales and intraday round trips are not modelled.",
)


@dataclass(frozen=True)
class Trade:
    day: date
    symbol: str
    side: str
    shares: int
    price: float


def read_trades(path: Path) -> list[Trade]:
    """Read and check a trades CSV; every problem is a ValueError."""
    if path.stat().st_size > MAX_BYTES:
        raise ValueError(f"{path.name} is larger than {MAX_BYTES // 2**20} MiB")
    trades = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.reader(source)
            header = [name.strip().lower() for name in next(reader, None) or []]
            if not set(COLUMNS) <= set(header):
                raise ValueError(f"A trades CSV needs the columns {', '.join(COLUMNS)}")
            column = {name: header.index(name) for name in COLUMNS}
            for line, row in enumerate(reader, start=2):
                if not row:
                    continue
                if len(trades) >= MAX_ROWS:
                    raise ValueError(f"{path.name} has more than {MAX_ROWS} trades")
                if len(row) < len(header):
                    raise ValueError(f"Line {line} has too few columns")
                trades.append(_trade({name: row[index].strip() for name, index in column.items()}, line))
    except csv.Error as exc:
        raise ValueError(f"Malformed CSV in {path.name}: {exc}") from None
    if not trades:
        raise ValueError(f"{path.name} has no trades")
    return trades


def _trade(row: dict[str, str], line: int) -> Trade:
    where = f"Line {line}"
    try:
        day = date.fromisoformat(row["date"])
    except ValueError:
        raise ValueError(f"{where}: {row['date']!r} is not a date (YYYY-MM-DD)") from None
    symbol = row["symbol"].upper()
    if not SYMBOL.fullmatch(symbol):
        raise ValueError(f"{where}: {symbol!r} is not a symbol Quantarium accepts")
    side = row["side"].lower()
    if side not in {"buy", "sell"}:
        raise ValueError(f"{where}: side must be buy or sell")
    try:
        shares_value = float(row["shares"])
        price = float(row["price"])
    except ValueError:
        raise ValueError(f"{where}: shares and price must be numbers") from None
    if not math.isfinite(shares_value) or shares_value <= 0 or shares_value != int(shares_value) \
            or shares_value > 10**9:
        raise ValueError(f"{where}: shares must be a positive whole number; fractional shares are not modelled")
    if not 0 < price < math.inf:
        raise ValueError(f"{where}: price must be positive")
    return Trade(day, symbol, side, int(shares_value), price)


def _net(trades: list[Trade]) -> dict[tuple[date, str], dict]:
    """Per day and symbol: the net side and shares, the real average price of that side, and round trips."""
    grouped: dict[tuple[date, str], dict] = defaultdict(lambda: {"buy": [0, 0.0], "sell": [0, 0.0]})
    for trade in trades:
        totals = grouped[(trade.day, trade.symbol)][trade.side]
        totals[0] += trade.shares
        totals[1] += trade.shares * trade.price
    result = {}
    for key, sides in grouped.items():
        bought, sold = sides["buy"][0], sides["sell"][0]
        side = "buy" if bought > sold else "sell" if sold > bought else None
        result[key] = {
            "side": side, "shares": abs(bought - sold),
            "real_price": sides[side][1] / sides[side][0] if side else None,
            "round_trip": bool(bought and sold),
        }
    return result


def _calendar(trades: list[Trade], prices: Path | None) -> list[date]:
    if prices is not None:
        from .data import load_daily_csv

        days, _, _ = load_daily_csv(prices, None, every_session=False)
        return days
    first, last = min(t.day for t in trades), max(t.day for t in trades)
    if first.year < market_calendar.FIRST_YEAR or (last - first).days > 366 * MAX_YEARS:
        raise ValueError(f"The NYSE calendar covers {market_calendar.FIRST_YEAR} on, for at most {MAX_YEARS} years "
                         f"of trades; pass --prices for other dates")
    return sorted(market_calendar.sessions(first - timedelta(days=14), last))


def trades_to_decisions(trades: list[Trade], *, prices: Path | None = None,
                        system: str = "trades-csv") -> tuple[dict, dict]:
    """A decision file document for the trades, and a report of what it cannot model."""
    calendar = _calendar(trades, prices)
    position = {day: index for index, day in enumerate(calendar)}
    netted = _net(trades)
    decisions: dict[date, list[dict]] = defaultdict(list)
    round_trips, held, short = [], defaultdict(int), defaultdict(int)
    for (day, symbol), item in sorted(netted.items()):
        if day not in position:
            raise ValueError(f"{day} is not a trading session" + (" in the price file" if prices else
                             " on the NYSE calendar; pass --prices for another market"))
        if position[day] == 0:
            raise ValueError(f"{day} is the calendar's first session, so no earlier close can decide it")
        if item["round_trip"]:
            round_trips.append(f"{day} {symbol}")
        if item["side"] is None:
            continue
        held[symbol] += item["shares"] if item["side"] == "buy" else -item["shares"]
        short[symbol] = max(short[symbol], -held[symbol])
        decisions[calendar[position[day] - 1]].append({
            "symbol": symbol, "side": item["side"], "kind": "market", "shares": item["shares"],
            "price": round(item["real_price"], 6), "tag": f"real {item['real_price']:.4f}"[:40]})
    symbols = sorted({trade.symbol for trade in trades})
    if len(symbols) > MAX_SYMBOLS:
        raise ValueError(f"A decision file holds at most {MAX_SYMBOLS} symbols")
    if len(decisions) > MAX_DECISIONS:
        raise ValueError(f"A decision file holds at most {MAX_DECISIONS} decisions")
    document = {
        "format": FORMAT, "source": {"system": system[:200], "version": "trades-csv-v1"}, "symbols": symbols,
        "decisions": [{"date": day.isoformat(), "weights": None, "orders": orders,
                       "note": f"Fills of {(calendar[position[day] + 1]).isoformat()} from the trades file"}
                      for day, orders in sorted(decisions.items())],
    }
    parse_decisions(json.dumps(document).encode())  # the result must read as any decision file does
    starting = {symbol: {"shares": shares} for symbol, shares in sorted(short.items()) if shares > 0}
    warnings = []
    if starting:
        warnings.append("Some symbols are sold before they are bought; give the cell these starting holdings: "
                        + ", ".join(f"{symbol} {value['shares']}" for symbol, value in starting.items()))
    if round_trips:
        warnings.append(f"{len(round_trips)} intraday round trip{'s are' if len(round_trips) > 1 else ' is'} netted "
                        f"out: {', '.join(round_trips[:10])}{', ...' if len(round_trips) > 10 else ''}")
    report = {
        "trades": len(trades), "decisions": len(document["decisions"]),
        "orders": sum(len(item["orders"]) for item in document["decisions"]), "symbols": symbols,
        "first_trade": min(t.day for t in trades).isoformat(), "last_trade": max(t.day for t in trades).isoformat(),
        "initial_positions": starting, "warnings": warnings, "limits": list(LIMITS),
    }
    return document, report


def write_decisions(trades_path: Path, output: Path, *, prices: Path | None = None,
                    system: str = "trades-csv", overwrite: bool = False) -> dict:
    if output.exists() and not overwrite:
        raise FileExistsError(f"{output} exists; pass --overwrite to replace it")
    document, report = trades_to_decisions(read_trades(trades_path), prices=prices, system=system)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return {**report, "output": str(output)}


def fill_gap(trades_path: Path, database: Path, cell: str, run_id: str | None = None) -> dict:
    """Quantarium's fills of a replayed trades file against the real ones.

    The gap is in basis points of the real price, signed so that a positive
    gap means Quantarium's fill was worse for the trader: a higher price for a
    buy, a lower one for a sell.
    """
    netted = _net(read_trades(trades_path))
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as db:
        if run_id is None:
            row = db.execute("SELECT r.id FROM runs r JOIN cells c ON c.run_id = r.id WHERE c.name = ? "
                             "ORDER BY r.created_utc DESC LIMIT 1", (cell,)).fetchone()
            if row is None:
                raise ValueError(f"No run of {cell} in {database}")
            run_id = row[0]
        fills: dict[tuple[date, str], list[tuple[str, int, float]]] = defaultdict(list)
        for day, payload in db.execute("SELECT date, payload_json FROM events WHERE run_id = ? AND cell_name = ? "
                                       "AND kind = 'order'", (run_id, cell)):
            order = json.loads(payload)
            if order.get("order_type") == "market" and order.get("filled_shares"):
                fills[(date.fromisoformat(day), order["symbol"])].append(
                    (order["side"], order["filled_shares"], order["fill_price"]))
    rows, unfilled = [], []
    for (day, symbol), item in sorted(netted.items()):
        if item["side"] is None:
            continue
        matching = [fill for fill in fills.get((day, symbol), []) if fill[0] == item["side"]]
        shares = sum(fill[1] for fill in matching)
        if not shares:
            unfilled.append(f"{day} {symbol} {item['side']} {item['shares']}")
            continue
        price = sum(fill[1] * fill[2] for fill in matching) / shares
        sign = 1 if item["side"] == "buy" else -1
        rows.append({"date": day.isoformat(), "symbol": symbol, "side": item["side"], "real_shares": item["shares"],
                     "filled_shares": shares, "real_price": item["real_price"], "quantarium_price": price,
                     "gap_bps": sign * (price - item["real_price"]) / item["real_price"] * 10_000,
                     "gap_usd": sign * (price - item["real_price"]) * shares})
    weights = sum(row["filled_shares"] * row["real_price"] for row in rows)
    return {
        "run_id": run_id, "cell": cell, "compared": len(rows), "unfilled": unfilled,
        "mean_gap_bps": (sum(row["gap_bps"] * row["filled_shares"] * row["real_price"] for row in rows) / weights
                         if weights else None),
        "total_gap_usd": sum(row["gap_usd"] for row in rows), "rows": rows,
        "meaning": "A positive gap means Quantarium's fill was worse for the trader than the real one.",
    }
