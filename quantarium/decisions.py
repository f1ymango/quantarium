"""Decision files: trading decisions made outside Quantarium, for Quantarium to replay and judge.

A decision is made at the close of its date, using information through that
close, and fills at the next open, as for Quantarium's own strategies. A file
holds data only; reading it never runs code. JSON is the exact format; CSV
carries weights only, for decisions kept in a spreadsheet. A decider's
tape (``.jsonl``: a header line, then one decision per line) reads the same
way, so a recorded run can be rescored. See
``docs/plans/v0.6.x-referee.md``.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .legacy import is_format
from . import strictjson
from .model import ORDER_KINDS, ORDER_SIDES, Order

FORMAT = "quantarium-decisions-v1"
MAX_BYTES = 10 * 1024 * 1024
MAX_DECISIONS = 50_000
MAX_SYMBOLS = 500
MAX_NOTE = 500
MAX_ORDERS = 100
SOURCE_FIELDS = ("system", "version", "model", "training_cutoff")
MAX_SOURCE_TEXT = 200
CSV_HEADER = ("date", "symbol", "weight")
TAPE_FORMAT = "quantarium-decision-tape-v1"
_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9.\-]{0,19}")
# Summing many weights can drift a little past 1.
WEIGHT_TOLERANCE = 1e-9


@dataclass(frozen=True)
class DecisionEntry:
    """One session's decision: target weights or None to hold, and orders or None to keep them."""

    weights: Mapping[str, float] | None
    orders: tuple[Order, ...] | None = None
    note: str = ""


@dataclass(frozen=True)
class DecisionFile:
    source: Mapping[str, str]
    symbols: tuple[str, ...]
    decisions: Mapping[date, DecisionEntry]
    sha256: str
    kind: str = "json"
    dates: tuple[date, ...] = field(default=())

    def entry(self, day: date) -> DecisionEntry | None:
        """The decision made at the close of ``day``, or None when the file has none: hold."""
        return self.decisions.get(day)

    def summary(self) -> dict:
        orders = sum(len(entry.orders or ()) for entry in self.decisions.values())
        return {"format": FORMAT, "kind": self.kind, "sha256": self.sha256, "source": dict(self.source),
                "symbols": list(self.symbols), "decisions": len(self.decisions), "orders": orders,
                "first_date": self.dates[0].isoformat() if self.dates else None,
                "last_date": self.dates[-1].isoformat() if self.dates else None}


def load_decisions(path: Path) -> DecisionFile:
    """Read and validate a decision file; every problem is a ValueError."""
    path = Path(path)
    if path.suffix.lower() not in {".json", ".csv", ".jsonl"}:
        raise ValueError("A decision file must be .json, .csv, or a decider's .jsonl tape")
    if path.stat().st_size > MAX_BYTES:
        raise ValueError(f"A decision file is limited to {MAX_BYTES // (1024 * 1024)} MB")
    raw = path.read_bytes()
    return parse_decisions(raw, {".csv": "csv", ".jsonl": "tape"}.get(path.suffix.lower(), "json"))


def parse_decisions(raw: bytes, kind: str = "json") -> DecisionFile:
    if len(raw) > MAX_BYTES:
        raise ValueError(f"A decision file is limited to {MAX_BYTES // (1024 * 1024)} MB")
    digest = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("A decision file must be UTF-8 text") from None
    if kind == "csv":
        source, symbols, entries = {}, None, _csv_entries(text)
    elif kind == "json":
        source, symbols, entries = _json_entries(text)
    elif kind == "tape":
        source, symbols, entries = _json_entries(_tape_document(text))
    else:
        raise ValueError("A decision file is JSON or CSV")
    if not entries:
        raise ValueError("A decision file needs at least one decision")
    if len(entries) > MAX_DECISIONS:
        raise ValueError(f"A decision file is limited to {MAX_DECISIONS:,} decisions")
    named = {symbol for _, entry in entries for symbol in (entry.weights or {})} | \
        {order.symbol for _, entry in entries for order in (entry.orders or ())}
    if symbols is None:
        symbols = tuple(sorted(named))
    elif not named <= set(symbols):
        raise ValueError(f"Decisions name symbols the file does not list: {', '.join(sorted(named - set(symbols)))}")
    if not symbols:
        raise ValueError("A decision file needs at least one symbol")
    if len(symbols) > MAX_SYMBOLS:
        raise ValueError(f"A decision file is limited to {MAX_SYMBOLS} symbols")
    decisions: dict[date, DecisionEntry] = {}
    for day, entry in entries:
        if day in decisions:
            raise ValueError(f"Two decisions are dated {day.isoformat()}")
        decisions[day] = entry
    dates = tuple(sorted(decisions))
    return DecisionFile(source=MappingProxyType(source), symbols=tuple(symbols),
                        decisions=MappingProxyType({day: decisions[day] for day in dates}),
                        sha256=digest, kind=kind, dates=dates)


def _json_entries(text: str) -> tuple[dict, tuple[str, ...], list[tuple[date, DecisionEntry]]]:
    document = strictjson.loads(text, limit=8)
    if not isinstance(document, dict) or not is_format(document.get("format"), "decisions"):
        raise ValueError(f"A decision file must be a JSON object with \"format\": \"{FORMAT}\"")
    unknown = set(document) - {"format", "source", "symbols", "decisions"}
    if unknown:
        raise ValueError(f"Unknown fields in the decision file: {', '.join(sorted(unknown))}")
    source = _source(document.get("source", {}))
    listed = document.get("symbols")
    if not isinstance(listed, list) or not listed:
        raise ValueError("symbols must be a nonempty list")
    symbols = tuple(_symbol(item) for item in listed)
    if len(set(symbols)) != len(symbols):
        raise ValueError("symbols must not repeat")
    items = document.get("decisions")
    if not isinstance(items, list):
        raise ValueError("decisions must be a list")
    if len(items) > MAX_DECISIONS:
        raise ValueError(f"A decision file is limited to {MAX_DECISIONS:,} decisions")
    return source, symbols, [_entry(item, index) for index, item in enumerate(items)]


def _tape_document(text: str) -> str:
    """A decider's tape as a decision document: its header names the source and symbols."""
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) > MAX_DECISIONS + 1:
        raise ValueError(f"A decision file is limited to {MAX_DECISIONS:,} decisions")
    if not lines:
        raise ValueError("A decider tape needs a header line")
    header = strictjson.loads(lines[0], limit=4)
    if not isinstance(header, dict) or not is_format(header.get("format"), "decision-tape"):
        raise ValueError(f"A decider tape starts with a \"format\": \"{TAPE_FORMAT}\" header")
    decisions = [strictjson.loads(line, limit=6) for line in lines[1:]]
    return json.dumps({"format": FORMAT, "source": header.get("source", {}), "symbols": header.get("symbols"),
                       "decisions": decisions})


def _csv_entries(text: str) -> list[tuple[date, DecisionEntry]]:
    reader = csv.reader(io.StringIO(text, newline=""))
    header = next(reader, None)
    if header is None or tuple(cell.strip().lower() for cell in header) != CSV_HEADER:
        raise ValueError(f"A decision CSV needs exactly the columns {', '.join(CSV_HEADER)}")
    days: dict[date, dict[str, float]] = {}
    for line, row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in row):
            continue
        if len(row) != len(CSV_HEADER):
            raise ValueError(f"CSV line {line} needs {len(CSV_HEADER)} columns")
        day, symbol = _date(row[0], f"CSV line {line}"), _symbol(row[1])
        weights = days.setdefault(day, {})
        if symbol in weights:
            raise ValueError(f"CSV line {line} repeats {symbol} on {day.isoformat()}")
        weights[symbol] = _weight(row[2], f"CSV line {line}")
        if len(days) > MAX_DECISIONS:
            raise ValueError(f"A decision file is limited to {MAX_DECISIONS:,} decisions")
    return [(day, DecisionEntry(_checked_weights(weights, day.isoformat()))) for day, weights in days.items()]


def _entry(item: object, index: int) -> tuple[date, DecisionEntry]:
    where = f"decisions[{index}]"
    if not isinstance(item, dict):
        raise ValueError(f"{where} must be an object")
    unknown = set(item) - {"date", "weights", "orders", "note"}
    if unknown:
        raise ValueError(f"Unknown fields in {where}: {', '.join(sorted(unknown))}")
    day = _date(item.get("date"), where)
    weights = item.get("weights")
    if weights is not None:
        if not isinstance(weights, dict):
            raise ValueError(f"{where}.weights must be an object of symbols to weights, or null to hold")
        weights = _checked_weights({_symbol(symbol): _weight(value, f"{where}.weights")
                                    for symbol, value in weights.items()}, where)
    orders = item.get("orders")
    if orders is not None:
        if not isinstance(orders, list) or len(orders) > MAX_ORDERS:
            raise ValueError(f"{where}.orders must be a list of at most {MAX_ORDERS} orders, or null")
        orders = tuple(_order(order, f"{where}.orders[{position}]") for position, order in enumerate(orders))
    return day, DecisionEntry(weights, orders, _text(item.get("note", ""), f"{where}.note", MAX_NOTE))


def _order(item: object, where: str) -> Order:
    if not isinstance(item, dict) or set(item) - {"symbol", "side", "kind", "shares", "price",
                                                  "expires_after_sessions", "tag"}:
        raise ValueError(f"{where} must be an object with symbol, side, kind, shares, and price")
    side, kind = item.get("side"), item.get("kind")
    if side not in ORDER_SIDES or kind not in ORDER_KINDS:
        raise ValueError(f"{where} needs side {' or '.join(ORDER_SIDES)} and kind {', '.join(ORDER_KINDS)}")
    shares = item.get("shares")
    if isinstance(shares, bool) or not isinstance(shares, int) or not 1 <= shares <= 10**9:
        raise ValueError(f"{where}.shares must be a positive whole number")
    price = item.get("price")
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
        raise ValueError(f"{where}.price must be a positive number")
    expires = item.get("expires_after_sessions")
    if expires is not None and (isinstance(expires, bool) or not isinstance(expires, int) or not 1 <= expires <= 10_000):
        raise ValueError(f"{where}.expires_after_sessions must be a positive whole number")
    return Order(_symbol(item.get("symbol")), side, kind, shares, float(price), expires,
                 _text(item.get("tag", ""), f"{where}.tag", 40))


def _source(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("source must be an object")
    unknown = set(value) - set(SOURCE_FIELDS)
    if unknown:
        raise ValueError(f"source may name only {', '.join(SOURCE_FIELDS)}")
    source = {key: _text(item, f"source.{key}", MAX_SOURCE_TEXT) for key, item in value.items()}
    if source.get("training_cutoff"):
        _date(source["training_cutoff"], "source.training_cutoff")
    return source


def _checked_weights(weights: dict[str, float], where: str) -> Mapping[str, float]:
    if sum(weights.values()) > 1 + WEIGHT_TOLERANCE:
        raise ValueError(f"Weights on {where} add up to more than 1")
    return MappingProxyType(weights)


def _weight(value: object, where: str) -> float:
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            raise ValueError(f"{where}: a weight must be a number") from None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or not 0 <= value <= 1:
        raise ValueError(f"{where}: weights must be numbers from 0 to 1")
    return float(value)


def _date(value: object, where: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{where} needs an ISO date such as 2030-01-07")
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(f"{where} needs an ISO date such as 2030-01-07") from None


def _symbol(value: object) -> str:
    symbol = value.strip().upper() if isinstance(value, str) else ""
    if not _SYMBOL.fullmatch(symbol):
        raise ValueError(f"{str(value)[:40]!r} is not a symbol: use letters, digits, dots, and dashes")
    return symbol


def _text(value: object, where: str, limit: int) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"{where} must be text of at most {limit} characters")
    if any(ord(char) < 32 and char not in "\t" or ord(char) == 127 for char in value):
        raise ValueError(f"{where} may not contain control characters")
    return value
