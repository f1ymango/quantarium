"""Validated, immutable daily-bar tape for deterministic static runs."""

from __future__ import annotations

import csv
import hashlib
import math
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Mapping

from .model import Bar, CorporateAction


def validate_bar(bar: Bar) -> None:
    # A chained comparison is false for NaN, infinities, zero, and negatives alike.
    if not (0 < bar.open < math.inf and 0 < bar.high < math.inf and 0 < bar.low < math.inf
            and 0 < bar.close < math.inf) or bar.volume < 0:
        raise ValueError(f"Invalid price or volume for {bar.symbol} on {bar.date}")
    if bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close):
        raise ValueError(f"Inconsistent OHLC values for {bar.symbol} on {bar.date}")


def load_daily_csv(path: Path, symbols: set[str] | None, rename: Mapping[str, str] | None = None,
                   *, every_session: bool = True) -> tuple[list[date], dict[date, dict[str, Bar]], str]:
    """Load a daily tape; ``rename`` maps file symbols to cell symbols.

    ``symbols=None`` keeps every symbol. With ``every_session``, each kept
    symbol needs a bar on every session; otherwise symbols may come and go.
    """
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    by_date: dict[date, dict[str, Bar]] = defaultdict(dict)
    # Bars share one string per symbol and one date per day: long tapes repeat both on every row.
    symbol_names: dict[str, str | None] = {}
    days_seen: dict[str, date] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.reader(source)
        header = next(reader, None)
        required = {"date", "symbol", "open", "high", "low", "close", "volume"}
        if not header or not required.issubset(header):
            raise ValueError(f"CSV needs columns: {', '.join(sorted(required))}")
        # The last of repeated column names wins, as with csv.DictReader.
        column = {name: index for index, name in enumerate(header)}
        width = len(header)
        i_date, i_symbol, i_open, i_high, i_low, i_close, i_volume = (
            column[name] for name in ("date", "symbol", "open", "high", "low", "close", "volume"))
        line = 1
        for row in reader:
            if not row:
                continue
            line += 1
            if len(row) < width:
                raise ValueError(f"CSV line {line} has too few columns")
            raw_symbol = row[i_symbol]
            symbol = symbol_names.get(raw_symbol, "")
            if symbol == "":
                symbol = raw_symbol.strip().upper()
                symbol = (rename or {}).get(symbol, symbol)
                symbol = symbol_names[raw_symbol] = symbol if symbols is None or symbol in symbols else None
            if symbol is None:
                continue
            try:
                raw_day = row[i_date]
                day = days_seen.get(raw_day)
                if day is None:
                    day = days_seen[raw_day] = date.fromisoformat(raw_day.strip())
                bar = Bar(
                    day, symbol, float(row[i_open]), float(row[i_high]),
                    float(row[i_low]), float(row[i_close]), int(row[i_volume]),
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid bar on CSV line {line}: {exc}") from exc
            try:
                validate_bar(bar)
            except ValueError as exc:
                raise ValueError(f"CSV line {line}: {exc}") from exc
            if symbol in by_date[day]:
                raise ValueError(f"Duplicate bar for {symbol} on {day}")
            by_date[day][symbol] = bar
    if not by_date:
        raise ValueError("No bars found for the requested symbols")
    days = sorted(by_date)
    for day in days if every_session else ():
        missing = (symbols if symbols is not None else set()) - by_date[day].keys()
        if missing:
            raise ValueError(f"Missing daily bars on {day}: {', '.join(sorted(missing))}")
    return days, dict(by_date), digest


def load_actions_csv(
    path: Path, symbols: set[str], sessions: set[date],
    rename: Mapping[str, str] | None = None,
) -> tuple[dict[date, list[CorporateAction]], str]:
    """Load explicitly dated split ratios and cash dividends for Static replay."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    by_date: dict[date, list[CorporateAction]] = defaultdict(list)
    seen_keys: set[str] = set()
    last_session = max(sessions)
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        required = {"date", "symbol", "kind", "value", "ex_date"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(f"Corporate-action CSV needs columns: {', '.join(sorted(required))}")
        for line, row in enumerate(reader, start=2):
            if None in row.values():
                raise ValueError(f"CSV line {line} has too few columns")
            symbol = row["symbol"].strip().upper()
            symbol = (rename or {}).get(symbol, symbol)
            if symbol not in symbols:
                continue
            try:
                day = date.fromisoformat(row["date"].strip())
                kind = row["kind"].strip().lower()
                value = float(row["value"])
                ex_date = date.fromisoformat(row["ex_date"].strip())
                source_id = (row.get("source_id") or "").strip() or None
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid corporate action on CSV line {line}: {exc}") from exc
            if (ex_date not in sessions or ex_date > day or kind not in {"split", "dividend"}
                    or (day not in sessions and not (kind == "dividend" and day > last_session))):
                raise ValueError(f"Invalid corporate-action date or kind on CSV line {line}")
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"Corporate-action value must be positive on CSV line {line}")
            if kind == "split" and ex_date != day:
                raise ValueError(f"Split ex_date must equal date on CSV line {line}")
            key = source_id or f"{symbol}:{day.isoformat()}"
            if key in seen_keys:
                raise ValueError(f"Duplicate corporate-action key on CSV line {line}: {key}")
            seen_keys.add(key)
            by_date[day].append(CorporateAction(day, symbol, kind, value, ex_date,
                                                source_id))
    return dict(by_date), digest

