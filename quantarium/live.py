"""One-session Live Feed simulation from finalized one-minute market bars."""

from __future__ import annotations

import hashlib
import asyncio
import json
import math
import os
import sqlite3
from collections import defaultdict, deque
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, TextIO

from .legacy import is_format
from .engine import _load_strategy, _rebalance, _validate_weights
from .model import BarHistory, CellSpec, LiveContext, LiveSpec, MinuteBar
from .provider import NEW_YORK


MINUTE = timedelta(minutes=1)


class ConcurrentLiveRunnerError(RuntimeError):
    """A different process advanced this experiment's checkpoint."""


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Live Feed timestamps must include a timezone")
    return value.astimezone(timezone.utc)


def _read_timestamp(value: str) -> datetime:
    return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _bar_body(bar: MinuteBar) -> str:
    return _canonical({
        "timestamp": bar.timestamp.isoformat(), "symbol": bar.symbol,
        "open": bar.open, "high": bar.high, "low": bar.low,
        "close": bar.close, "volume": bar.volume,
    })


def validate_minute_bar(bar: MinuteBar) -> None:
    stamp = _utc(bar.timestamp)
    if stamp != bar.timestamp or stamp.second or stamp.microsecond:
        raise ValueError("Minute bar timestamp must be a whole UTC minute")
    prices = (bar.open, bar.high, bar.low, bar.close)
    if (not bar.symbol or any(not math.isfinite(price) or price <= 0 for price in prices)
            or isinstance(bar.volume, bool) or not isinstance(bar.volume, int)
            or bar.volume < 0):
        raise ValueError(f"Invalid minute bar for {bar.symbol} at {stamp}")
    if (bar.low > min(bar.open, bar.close)
            or bar.high < max(bar.open, bar.close) or bar.high < bar.low):
        raise ValueError(f"Inconsistent minute OHLC for {bar.symbol} at {stamp}")


def parse_alpaca_minute_event(item: dict[str, object]) -> tuple[MinuteBar, str] | None:
    """Convert Alpaca's bar/update message to a validated UTC minute bar."""
    kind = item.get("T")
    if kind not in {"b", "u"}:
        return None
    try:
        volume = item["v"]
        if isinstance(volume, bool) or not isinstance(volume, int):
            raise ValueError("Volume must be an integer")
        stamp = _read_timestamp(str(item["t"]))
        bar = MinuteBar(stamp, str(item["S"]), float(item["o"]),
                        float(item["h"]), float(item["l"]),
                        float(item["c"]), volume)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Alpaca sent an invalid minute bar") from exc
    validate_minute_bar(bar)
    return bar, str(kind)


class LiveCellRuntime:
    """An independent account and strategy set over shared bounded bar history."""

    def __init__(self, cell: CellSpec, spec: LiveSpec, strategy_root: Path,
                 history: dict[str, deque[MinuteBar]], saved: dict | None = None):
        if cell.model_card is not None or cell.ask is not None:
            raise ValueError("Daily model cards and context.ask cannot run on Live Feed minute bars")
        self.cell, self.spec, self.history = cell, spec, history
        self.cash = cell.initial_cash
        self.positions = {symbol: 0 for symbol in cell.symbols}
        self.equity = cell.initial_cash
        self.last_timestamp: datetime | None = None
        self.bar_count = 0
        self.pending: list[dict] = []
        self.strategies: list[tuple[object, object]] = []
        self.source_hashes: list[str | None] = []
        for strategy_spec in cell.strategies:
            strategy, source_hash = _load_strategy(
                strategy_spec.entrypoint, dict(strategy_spec.parameters), strategy_root
            )
            snapshot = getattr(strategy, "snapshot", None)
            restore = getattr(strategy, "restore", None)
            if callable(snapshot) != callable(restore):
                raise ValueError(f"{strategy_spec.entrypoint} must implement both snapshot and restore")
            self.strategies.append((strategy_spec, strategy))
            self.source_hashes.append(source_hash)
        if saved is not None:
            self.restore(saved)

    def restore(self, saved: dict) -> None:
        if (set(saved["positions"]) != set(self.cell.symbols)
                or len(saved["strategy_states"]) != len(self.strategies)):
            raise ValueError(f"Invalid Live Feed checkpoint for {self.cell.name}")
        self.cash = float(saved["cash"])
        self.positions = dict(saved["positions"])
        self.equity = float(saved["equity"])
        self.last_timestamp = (_read_timestamp(saved["last_timestamp"])
                               if saved["last_timestamp"] else None)
        self.bar_count = int(saved["bar_count"])
        self.pending = list(saved["pending"])
        for index, (_, strategy) in enumerate(self.strategies):
            restore = getattr(strategy, "restore", None)
            if callable(restore):
                restore(saved["strategy_states"][index])

    def checkpoint(self) -> dict:
        states = []
        for _, strategy in self.strategies:
            snapshot = getattr(strategy, "snapshot", None)
            states.append(snapshot() if callable(snapshot) else None)
        return {
            "cash": self.cash, "positions": self.positions.copy(),
            "equity": self.equity,
            "last_timestamp": self.last_timestamp.isoformat() if self.last_timestamp else None,
            "bar_count": self.bar_count,
            "pending": [dict(item) for item in self.pending],
            "strategy_states": states,
        }

    def process(self, stamp: datetime, bars: dict[str, MinuteBar],
                available_at: datetime, journal: Any,
                fresh: frozenset[str] | None = None,
                snapshots: dict[str, tuple[MinuteBar, ...]] | None = None) -> None:
        """Advance one minute.

        ``bars`` holds each symbol's latest observed bar; ``fresh`` names the
        symbols whose bar belongs to ``stamp``. The others were quiet this
        minute and appear with an earlier bar, or not at all if they have never
        traded in this session.
        """
        if self.last_timestamp is not None and stamp != self.last_timestamp + MINUTE:
            raise ValueError(f"Non-consecutive Live Feed minute for {self.cell.name}")
        fresh = frozenset(bars) if fresh is None else fresh
        if (not set(bars) <= set(self.cell.symbols) or not fresh <= set(bars)
                or any(bars[symbol].timestamp != stamp for symbol in fresh)
                or any(bars[symbol].timestamp >= stamp for symbol in set(bars) - fresh)):
            raise ValueError(f"Inconsistent Live Feed minute for {self.cell.name}")
        quiet = frozenset(bars) - fresh
        key = stamp.isoformat()
        eligible = [item for item in self.pending
                    if _read_timestamp(item["eligible_at"]) <= stamp]
        if eligible:
            chosen = eligible[-1]
            if len(eligible) > 1:
                journal.event(self.cell.name, key, "signals_superseded", {
                    "signal_timestamps": [item["signal_at"] for item in eligible[:-1]],
                })
            self.cash = _rebalance(
                self.cell, stamp, bars, chosen["weights"], self.cash,
                self.positions, self.spec.slippage_bps,
                self.spec.commission_per_order, journal,
                {symbol: self.history[symbol][-1].volume
                 for symbol in fresh if self.history[symbol]},
                quiet,
            )
            self.pending = [item for item in self.pending if item not in eligible]
        self.equity = self.cash + sum(
            self.positions[symbol] * bars[symbol].close for symbol in bars
        )
        valuation = {
            "cash": self.cash, "positions": self.positions.copy(), "equity": self.equity,
            "prices": {symbol: bars[symbol].close for symbol in sorted(bars)},
        }
        if set(bars) != set(self.cell.symbols) or quiet:
            valuation["quiet_symbols"] = sorted(set(self.cell.symbols) - fresh)
        journal.event(self.cell.name, key, "valuation", valuation)
        snapshots = snapshots or {}
        context = LiveContext(
            timestamp=stamp, date=stamp.astimezone(NEW_YORK).date(),
            bars=MappingProxyType(bars.copy()),
            history=MappingProxyType({
                symbol: BarHistory(snapshots.get(symbol, self.history[symbol]),
                                   bars[symbol] if symbol in fresh else None)
                for symbol in self.cell.symbols
            }),
            cash=self.cash, positions=MappingProxyType(self.positions.copy()),
            equity=self.equity,
            quiet_symbols=frozenset(self.cell.symbols) - fresh,
        )
        self.bar_count += 1
        if (self.bar_count - 1) % self.cell.rebalance_every_sessions:
            journal.event(self.cell.name, key, "rebalance_skipped", {
                "bar_number": self.bar_count,
                "rebalance_every_sessions": self.cell.rebalance_every_sessions,
            })
        else:
            combined: dict[str, float] = defaultdict(float)
            decisions = []
            for strategy_spec, strategy in self.strategies:
                weights = _validate_weights(strategy.on_bar(context), self.cell)
                for symbol, weight in weights.items():
                    combined[symbol] += strategy_spec.allocation * weight
                decisions.append({"entrypoint": strategy_spec.entrypoint,
                                  "weights": weights})
            for symbol, weight in combined.items():
                if weight > self.cell.max_position_weight + 1e-12:
                    raise ValueError(
                        f"Target weight for {symbol} in {self.cell.name} exceeds max_position_weight"
                    )
            if sum(combined.values()) > 1 - self.cell.min_cash_weight + 1e-12:
                raise ValueError(f"Target weights in {self.cell.name} violate min_cash_weight")
            next_open = available_at.replace(second=0, microsecond=0) + MINUTE
            self.pending.append({"signal_at": key, "eligible_at": next_open.isoformat(),
                                 "weights": dict(combined)})
            journal.event(self.cell.name, key, "signal", {
                "decisions": decisions, "target_weights": dict(combined),
                "available_at": available_at.isoformat(),
                "earliest_fill_at": next_open.isoformat(),
            })
        self.last_timestamp = stamp


class LiveStore:
    def __init__(self, spec: LiveSpec, config_json: str,
                 source_hashes: dict[str, list[str | None]]):
        path = Path(spec.database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self.spec = spec
        self.failed = False
        self.sequences: dict[str, int] = {}
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS live_experiments (
                id TEXT PRIMARY KEY,
                config_sha256 TEXT NOT NULL,
                created_utc TEXT NOT NULL,
                provider TEXT NOT NULL,
                market_feed TEXT NOT NULL,
                start_at TEXT NOT NULL,
                stop_at TEXT NOT NULL,
                input_kind TEXT NOT NULL DEFAULT 'websocket',
                input_sha256 TEXT,
                status TEXT NOT NULL,
                stop_reason TEXT
            );
            CREATE TABLE IF NOT EXISTS live_cells (
                feed_id TEXT NOT NULL REFERENCES live_experiments(id),
                name TEXT NOT NULL,
                mode TEXT NOT NULL,
                initial_cash REAL NOT NULL,
                source_hashes_json TEXT NOT NULL,
                state_json TEXT NOT NULL,
                sequence INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',
                error TEXT,
                PRIMARY KEY (feed_id, name)
            );
            CREATE TABLE IF NOT EXISTS live_bars (
                feed_id TEXT NOT NULL REFERENCES live_experiments(id),
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume INTEGER NOT NULL,
                finalized_at TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                PRIMARY KEY (feed_id, timestamp, symbol)
            );
            CREATE INDEX IF NOT EXISTS live_bars_symbol_time
                ON live_bars(feed_id, symbol, timestamp DESC);
            CREATE TABLE IF NOT EXISTS live_events (
                feed_id TEXT NOT NULL,
                cell_name TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (feed_id, cell_name, sequence)
            );
            CREATE TABLE IF NOT EXISTS live_revisions (
                feed_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                original_sha256 TEXT NOT NULL,
                revised_sha256 TEXT NOT NULL,
                revised_bar_json TEXT NOT NULL,
                PRIMARY KEY (feed_id, timestamp, symbol, revised_sha256)
            );
            CREATE TABLE IF NOT EXISTS live_minutes (
                feed_id TEXT NOT NULL REFERENCES live_experiments(id),
                timestamp TEXT NOT NULL,
                bar_count INTEGER NOT NULL,
                finalized_at TEXT NOT NULL,
                PRIMARY KEY (feed_id, timestamp)
            );
        """)
        # Experiments saved before live_minutes existed required every symbol in
        # every minute, so their processed minutes are exactly their bar times.
        self.connection.execute("""
            INSERT INTO live_minutes(feed_id, timestamp, bar_count, finalized_at)
            SELECT feed_id, timestamp, COUNT(*), MIN(finalized_at) FROM live_bars
            WHERE feed_id NOT IN (SELECT DISTINCT feed_id FROM live_minutes)
            GROUP BY feed_id, timestamp
        """)
        self.connection.commit()
        columns = {row[1] for row in self.connection.execute(
            "PRAGMA table_info(live_experiments)"
        )}
        if "input_kind" not in columns:
            self.connection.execute(
                "ALTER TABLE live_experiments ADD COLUMN input_kind "
                "TEXT NOT NULL DEFAULT 'websocket'"
            )
        if "input_sha256" not in columns:
            self.connection.execute(
                "ALTER TABLE live_experiments ADD COLUMN input_sha256 TEXT"
            )
        cell_columns = {row[1] for row in self.connection.execute(
            "PRAGMA table_info(live_cells)"
        )}
        if "status" not in cell_columns:
            self.connection.execute(
                "ALTER TABLE live_cells ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"
            )
        if "error" not in cell_columns:
            self.connection.execute("ALTER TABLE live_cells ADD COLUMN error TEXT")
        config_hash = hashlib.sha256(_canonical(json.loads(config_json)).encode()).hexdigest()
        row = self.connection.execute(
            "SELECT config_sha256, status, input_kind, input_sha256 "
            "FROM live_experiments WHERE id=?", (spec.feed_id,)
        ).fetchone()
        if row is not None and row[0] != config_hash:
            self.close()
            raise ValueError("Live Feed configuration changed; use a new feed_id")
        if row is None:
            self.connection.execute(
                "INSERT INTO live_experiments "
                "(id, config_sha256, created_utc, provider, market_feed, start_at, "
                "stop_at, input_kind, input_sha256, status, stop_reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', NULL)",
                (spec.feed_id, config_hash, datetime.now(timezone.utc).isoformat(),
                 spec.provider, spec.market_feed, spec.start_at.isoformat(),
                 spec.stop_at.isoformat(), spec.input_kind, spec.input_sha256),
            )
            for cell in spec.cells:
                state = {
                    "cash": cell.initial_cash, "positions": {s: 0 for s in cell.symbols},
                    "equity": cell.initial_cash, "last_timestamp": None, "bar_count": 0,
                    "pending": [], "strategy_states": [None for _ in cell.strategies],
                }
                self.connection.execute(
                    "INSERT INTO live_cells(feed_id, name, mode, initial_cash, "
                    "source_hashes_json, state_json) VALUES (?, ?, ?, ?, ?, ?)",
                    (spec.feed_id, cell.name, cell.mode, cell.initial_cash,
                     _canonical(source_hashes[cell.name]), _canonical(state)),
                )
            self.connection.commit()
        elif row[1] != "active":
            self.close()
            raise ValueError(f"Live Feed is {row[1]}; use a new feed_id")
        elif row[2:] != (spec.input_kind, spec.input_sha256):
            self.close()
            raise ValueError("Live Feed input changed; use a new feed_id")
        for cell in spec.cells:
            row = self.connection.execute(
                "SELECT source_hashes_json, sequence FROM live_cells WHERE feed_id=? AND name=?",
                (spec.feed_id, cell.name),
            ).fetchone()
            if row is None or json.loads(row[0]) != source_hashes[cell.name]:
                self.close()
                raise ValueError(f"Live strategy source changed in {cell.name}; use a new feed_id")
            self.sequences[cell.name] = row[1]

    def close(self) -> None:
        self.connection.close()

    def state(self, name: str) -> dict:
        row = self.connection.execute(
            "SELECT state_json FROM live_cells WHERE feed_id=? AND name=?",
            (self.spec.feed_id, name),
        ).fetchone()
        if row is None:
            raise ValueError(f"Live Feed cell not found: {name}")
        return json.loads(row[0])

    def last_timestamp(self) -> datetime | None:
        value = self.connection.execute(
            "SELECT MAX(timestamp) FROM live_minutes WHERE feed_id=?",
            (self.spec.feed_id,),
        ).fetchone()[0]
        return _read_timestamp(value) if value else None

    def saved_bars_consistent(self) -> bool:
        """Whether every processed minute still has exactly its recorded bars."""
        return self.connection.execute(
            "SELECT NOT EXISTS ("
            " SELECT 1 FROM live_minutes m WHERE m.feed_id=:id AND m.bar_count != ("
            "  SELECT COUNT(*) FROM live_bars b"
            "  WHERE b.feed_id=m.feed_id AND b.timestamp=m.timestamp))"
            " AND NOT EXISTS ("
            " SELECT 1 FROM live_bars b WHERE b.feed_id=:id AND NOT EXISTS ("
            "  SELECT 1 FROM live_minutes m"
            "  WHERE m.feed_id=b.feed_id AND m.timestamp=b.timestamp))",
            {"id": self.spec.feed_id},
        ).fetchone()[0] == 1

    def active_cells(self) -> set[str]:
        return {row[0] for row in self.connection.execute(
            "SELECT name FROM live_cells WHERE feed_id=? AND status='active'",
            (self.spec.feed_id,),
        )}

    def history(self, symbols: set[str]) -> dict[str, deque[MinuteBar]]:
        result: dict[str, deque[MinuteBar]] = {}
        for symbol in symbols:
            rows = self.connection.execute(
                "SELECT timestamp, open, high, low, close, volume FROM live_bars "
                "WHERE feed_id=? AND symbol=? ORDER BY timestamp DESC LIMIT ?",
                (self.spec.feed_id, symbol, self.spec.history_limit),
            ).fetchall()
            result[symbol] = deque(
                (MinuteBar(_read_timestamp(row[0]), symbol, *row[1:])
                 for row in reversed(rows)), maxlen=self.spec.history_limit,
            )
        return result

    def event(self, cell_name: str, timestamp: str, kind: str, payload: object) -> None:
        self.sequences[cell_name] += 1
        self.connection.execute(
            "INSERT INTO live_events VALUES (?, ?, ?, ?, ?, ?)",
            (self.spec.feed_id, cell_name, self.sequences[cell_name],
             timestamp, kind, _canonical(payload)),
        )

    def process_minute(self, stamp: datetime, bars: dict[str, MinuteBar],
                       available_at: datetime, runtimes: dict[str, LiveCellRuntime],
                       history: dict[str, deque[MinuteBar]]) -> set[str]:
        if self.failed:
            raise RuntimeError("This Live Feed runner failed; reopen it from the checkpoint")
        prior = self.last_timestamp()
        expected = self.spec.start_at if prior is None else prior + MINUTE
        if stamp != expected:
            raise ValueError(f"Expected Live Feed minute {expected}, received {stamp}")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            if self.last_timestamp() != prior:
                raise ConcurrentLiveRunnerError("Another process advanced this Live Feed")
            # retire-cell may have written an event from another process.
            self.sequences.update(self.connection.execute(
                "SELECT name, sequence FROM live_cells WHERE feed_id=?", (self.spec.feed_id,),
            ))
            for symbol, bar in bars.items():
                digest = hashlib.sha256(_bar_body(bar).encode()).hexdigest()
                self.connection.execute(
                    "INSERT INTO live_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (self.spec.feed_id, stamp.isoformat(), symbol, bar.open, bar.high,
                     bar.low, bar.close, bar.volume, available_at.isoformat(), digest),
                )
            self.connection.execute(
                "INSERT INTO live_minutes VALUES (?, ?, ?, ?)",
                (self.spec.feed_id, stamp.isoformat(), len(bars), available_at.isoformat()),
            )
            # One copy of each symbol's prior bars per minute, shared by every
            # cell, instead of one copy per cell.
            snapshots = {symbol: tuple(values) for symbol, values in history.items()}
            failed_cells: set[str] = set()
            active_cells = self.active_cells()
            for cell in self.spec.cells:
                if cell.name not in active_cells:
                    continue
                runtime = runtimes[cell.name]
                prior_sequence = self.sequences[cell.name]
                self.connection.execute("SAVEPOINT live_cell")
                try:
                    if prior is None:
                        for (strategy_spec, _), source_hash in zip(
                            runtime.strategies, runtime.source_hashes
                        ):
                            self.event(cell.name, stamp.isoformat(), "strategy", {
                                "entrypoint": strategy_spec.entrypoint,
                                "allocation": strategy_spec.allocation,
                                "parameters": strategy_spec.parameters,
                                "source_sha256": source_hash,
                            })
                    latest = {
                        symbol: bars[symbol] if symbol in bars else history[symbol][-1]
                        for symbol in cell.symbols if symbol in bars or history[symbol]
                    }
                    runtime.process(stamp, latest, available_at, self,
                                    frozenset(bars) & frozenset(cell.symbols), snapshots)
                    self.connection.execute(
                        "UPDATE live_cells SET state_json=?, sequence=? "
                        "WHERE feed_id=? AND name=?",
                        (_canonical(runtime.checkpoint()), self.sequences[cell.name],
                         self.spec.feed_id, cell.name),
                    )
                except Exception as exc:
                    if isinstance(exc, (sqlite3.Error, ConcurrentLiveRunnerError)):
                        raise
                    self.connection.execute("ROLLBACK TO SAVEPOINT live_cell")
                    self.connection.execute("RELEASE SAVEPOINT live_cell")
                    self.sequences[cell.name] = prior_sequence
                    reason = f"{type(exc).__name__}: {exc}"
                    self.event(cell.name, stamp.isoformat(), "cell_failed", {
                        "reason": reason,
                    })
                    self.connection.execute(
                        "UPDATE live_cells SET status='failed', error=?, sequence=? "
                        "WHERE feed_id=? AND name=?",
                        (reason, self.sequences[cell.name],
                         self.spec.feed_id, cell.name),
                    )
                    failed_cells.add(cell.name)
                else:
                    self.connection.execute("RELEASE SAVEPOINT live_cell")
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            self.failed = True
            raise
        for symbol, bar in bars.items():
            history[symbol].append(bar)
        return failed_cells

    def record_revision(self, bar: MinuteBar, observed_at: datetime) -> None:
        row = self.connection.execute(
            "SELECT sha256 FROM live_bars WHERE feed_id=? AND timestamp=? AND symbol=?",
            (self.spec.feed_id, bar.timestamp.isoformat(), bar.symbol),
        ).fetchone()
        # A quiet minute has no first-seen bar; its late arrival is still a
        # revision of what the cells were shown, recorded with an empty original.
        original = "" if row is None else row[0]
        body = _bar_body(bar)
        digest = hashlib.sha256(body.encode()).hexdigest()
        if digest != original:
            self.connection.execute(
                "INSERT OR IGNORE INTO live_revisions VALUES (?, ?, ?, ?, ?, ?, ?)",
                (self.spec.feed_id, bar.timestamp.isoformat(), bar.symbol,
                 observed_at.isoformat(), original, digest, body),
            )
            self.connection.commit()

    def stop(self, status: str, reason: str | None = None) -> None:
        if status not in {"complete", "halted"}:
            raise ValueError("Invalid Live Feed stop status")
        changed = self.connection.execute(
            "UPDATE live_experiments SET status=?, stop_reason=? WHERE id=? AND status='active'",
            (status, reason, self.spec.feed_id),
        )
        if changed.rowcount:
            cell_status = "completed" if status == "complete" else "stopped"
            self.connection.execute(
                "UPDATE live_cells SET status=? WHERE feed_id=? AND status='active'",
                (cell_status, self.spec.feed_id),
            )
        self.connection.commit()

    def invalidate_replay(self, reason: str) -> None:
        if self.spec.input_kind != "replay":
            raise ValueError("Only replay experiments can be invalidated this way")
        self.connection.execute(
            "UPDATE live_experiments SET status='halted', stop_reason=? WHERE id=?",
            (reason, self.spec.feed_id),
        )
        self.connection.execute(
            "UPDATE live_cells SET status='stopped' WHERE feed_id=? "
            "AND status IN ('active', 'completed')",
            (self.spec.feed_id,),
        )
        self.connection.commit()

    def report(self) -> dict:
        return _report(self.connection, self.spec.feed_id)


def _report(connection: sqlite3.Connection, feed_id: str) -> dict:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(live_experiments)")}
    input_kind = "input_kind" if "input_kind" in columns else "'websocket'"
    input_sha256 = "input_sha256" if "input_sha256" in columns else "NULL"
    row = connection.execute(
        "SELECT created_utc, provider, market_feed, start_at, stop_at, "
        f"{input_kind}, {input_sha256}, status, stop_reason "
        "FROM live_experiments WHERE id=?", (feed_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Live Feed experiment not found: {feed_id}")
    cells = []
    cell_columns = {row[1] for row in connection.execute("PRAGMA table_info(live_cells)")}
    cell_status = "status" if "status" in cell_columns else "'active'"
    cell_error = "error" if "error" in cell_columns else "NULL"
    for name, mode, initial_cash, state_json, status, error in connection.execute(
        f"SELECT name, mode, initial_cash, state_json, {cell_status}, {cell_error} "
        "FROM live_cells "
        "WHERE feed_id=? ORDER BY name", (feed_id,),
    ):
        if status == "active" and row[7] in {"complete", "halted"}:
            status = "completed" if row[7] == "complete" else "stopped"
        state = json.loads(state_json)
        cells.append({
            "name": name, "mode": mode, "initial_cash": initial_cash,
            "cash": state["cash"], "positions": state["positions"],
            "equity": state["equity"], "return_pct": 100 * (state["equity"] / initial_cash - 1),
            "last_timestamp": state["last_timestamp"], "bars_processed": state["bar_count"],
            "pending_signals": len(state["pending"]),
            "status": status, "error": error,
        })
    bars = connection.execute(
        "SELECT COUNT(*) FROM live_bars WHERE feed_id=?", (feed_id,),
    ).fetchone()[0]
    revisions = connection.execute(
        "SELECT COUNT(*) FROM live_revisions WHERE feed_id=?", (feed_id,),
    ).fetchone()[0]
    has_minutes = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='live_minutes'"
    ).fetchone()
    minutes = connection.execute(
        "SELECT COUNT(*) FROM live_minutes WHERE feed_id=?" if has_minutes else
        "SELECT COUNT(DISTINCT timestamp) FROM live_bars WHERE feed_id=?", (feed_id,),
    ).fetchone()[0]
    symbol_count = len({symbol for cell in cells for symbol in cell["positions"]})
    return {
        "feed_id": feed_id, "created_utc": row[0], "provider": row[1],
        "market_feed": row[2], "start_at": row[3], "stop_at": row[4],
        "input_kind": row[5], "input_sha256": row[6],
        "status": row[7], "stop_reason": row[8],
        "bars_cached": bars, "minutes_processed": minutes,
        "quiet_symbol_minutes": minutes * symbol_count - bars,
        "revisions_observed": revisions, "cells": cells,
    }


def read_live_report(path: Path, feed_id: str) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        return _report(connection, feed_id)
    finally:
        connection.close()


class LiveFeed:
    """Fan out one complete, finalized market minute to every configured cell."""

    def __init__(self, spec: LiveSpec, config_json: str, strategy_root: Path):
        if spec.input_kind not in {"websocket", "replay"}:
            raise ValueError("Invalid Live Feed input kind")
        if (spec.input_kind == "replay") != (spec.input_sha256 is not None):
            raise ValueError("Replay input must have a file SHA-256; WebSocket input must not")
        self.spec = spec
        self.symbols = {s for cell in spec.cells for s in cell.symbols}
        provisional_history = {s: deque(maxlen=spec.history_limit) for s in self.symbols}
        provisional = {cell.name: LiveCellRuntime(
            cell, spec, strategy_root,
            {s: provisional_history[s] for s in cell.symbols}
        ) for cell in spec.cells}
        hashes = {name: runtime.source_hashes for name, runtime in provisional.items()}
        if any(value is None for values in hashes.values() for value in values):
            raise ValueError("Live Feed strategies must have source files for source pinning")
        self.store = LiveStore(spec, config_json, hashes)
        self.history = self.store.history(self.symbols)
        self.runtimes = provisional
        for cell in spec.cells:
            runtime = self.runtimes[cell.name]
            runtime.history = {s: self.history[s] for s in cell.symbols}
            runtime.restore(self.store.state(cell.name))
        self.pending: dict[datetime, dict[str, MinuteBar]] = {}
        self.last = self.store.last_timestamp()
        self.active_cells = self.store.active_cells()
        expected_count = (0 if self.last is None else
                          int((self.last - spec.start_at) / MINUTE) + 1)
        if (self.last is not None and (
                self.last < spec.start_at or self.last >= spec.stop_at
                or (self.last - spec.start_at) % MINUTE)
                or expected_count < 0
                or expected_count > int((spec.stop_at - spec.start_at) / MINUTE)
                or any(self.runtimes[name].bar_count != expected_count
                       for name in self.active_cells)
                or not self.store.saved_bars_consistent()
                or (expected_count and any(
                    self.history[symbol] and self.history[symbol][-1].timestamp > self.last
                    for symbol in self.symbols
                ))):
            self.store.close()
            raise ValueError("Live Feed checkpoint and saved minute bars disagree")
        self.last_clock: datetime | None = None
        self.finished = False

    def close(self) -> None:
        self.store.close()

    def accept(self, bar: MinuteBar, observed_at: datetime) -> None:
        validate_minute_bar(bar)
        observed_at = _utc(observed_at)
        if bar.symbol not in self.symbols:
            raise ValueError(f"Unexpected Live Feed symbol: {bar.symbol}")
        if not self.spec.start_at <= bar.timestamp < self.spec.stop_at:
            return
        if observed_at < bar.timestamp + MINUTE:
            raise ValueError("Minute bar arrived before its close")
        if self.last is not None and bar.timestamp <= self.last:
            self.store.record_revision(bar, observed_at)
            return
        self.pending.setdefault(bar.timestamp, {})[bar.symbol] = bar

    def flush(self, now: datetime) -> int:
        now = _utc(now)
        if self.last_clock is not None and now < self.last_clock:
            raise ValueError("Live Feed clock moved backward")
        self.last_clock = now
        processed = 0
        while not self.finished:
            if self.store.last_timestamp() != self.last:
                raise ConcurrentLiveRunnerError("Another process advanced this Live Feed")
            expected = self.spec.start_at if self.last is None else self.last + MINUTE
            if expected >= self.spec.stop_at:
                self.store.stop("complete")
                self.finished = True
                break
            if now < expected + timedelta(seconds=self.spec.finalization_delay_seconds):
                break
            bars = self.pending.get(expected, {})
            too_quiet = sorted(symbol for symbol in self.symbols - bars.keys()
                               if self.quiet_minutes(symbol, expected)
                               > self.spec.max_quiet_minutes)
            if too_quiet:
                if self.spec.max_quiet_minutes:
                    reason = (f"No bar for more than {self.spec.max_quiet_minutes} "
                              f"minutes at {expected.isoformat()}: {', '.join(too_quiet)}")
                else:
                    reason = (f"Incomplete minute {expected.isoformat()}: "
                              f"missing {', '.join(too_quiet)}")
                self.store.stop("halted", reason)
                self.finished = True
                raise ValueError(reason)
            if now >= expected + 2 * MINUTE:
                reason = f"Minute {expected.isoformat()} finalized too late for a live decision"
                self.store.stop("halted", reason)
                self.finished = True
                raise ValueError(reason)
            failed_cells = self.store.process_minute(
                expected, bars, now, self.runtimes, self.history
            )
            self.active_cells = self.store.active_cells()
            self.pending.pop(expected, None)
            self.last = expected
            processed += 1
            if not self.active_cells:
                self.store.stop("halted", "No active Live Feed cells remain"
                                if not failed_cells else "All Live Feed cells failed")
                self.finished = True
        return processed

    def quiet_minutes(self, symbol: str, stamp: datetime) -> int:
        """Consecutive minutes through ``stamp`` without a bar for ``symbol``."""
        values = self.history[symbol]
        last_seen = values[-1].timestamp if values else self.spec.start_at - MINUTE
        return int((stamp - last_seen) / MINUTE)

    def report(self) -> dict:
        return self.store.report()


def _stream_items(frame: str | bytes) -> list[dict]:
    payload = json.loads(frame)
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise ValueError("Alpaca sent an invalid stream frame")
    return payload


async def _await_control(socket: Any, expected: str) -> dict:
    while True:
        frame = await asyncio.wait_for(socket.recv(), timeout=10)
        for item in _stream_items(frame):
            if item.get("T") == "error":
                raise RuntimeError(f"Alpaca stream error {item.get('code')}: {item.get('msg')}")
            if item.get("T") == "success" and item.get("msg") == expected:
                return item
            if expected == "subscription" and item.get("T") == "subscription":
                return item


def _capture_event(destination: TextIO, observed_at: datetime,
                   items: list[dict]) -> None:
    destination.write(_canonical({
        "received_at": _utc(observed_at).isoformat(), "items": items,
    }) + "\n")
    destination.flush()


async def run_live_feed(spec: LiveSpec, config_json: str, strategy_root: Path,
                        *, connector: Any = None, clock: Any = None,
                        capture_path: Path | None = None) -> dict:
    """Run one Alpaca WebSocket for the union of all cells' symbols."""
    if spec.input_kind != "websocket":
        raise ValueError("Live WebSocket runner requires websocket input")
    if connector is None:
        try:
            from websockets.asyncio.client import connect
        except ImportError as exc:
            raise RuntimeError("Install Quantarium with the live extra: pip install -e '.[live]'") from exc
        connector = connect
    clock = clock or (lambda: datetime.now(timezone.utc))
    key, secret = os.getenv("APCA_API_KEY_ID"), os.getenv("APCA_API_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("Set APCA_API_KEY_ID and APCA_API_SECRET_KEY for Live Feed")
    if capture_path is not None and capture_path.exists():
        raise FileExistsError(f"Live capture already exists: {capture_path}")
    feed = LiveFeed(spec, config_json, strategy_root)
    capture: TextIO | None = None
    stream_started = False
    try:
        if capture_path is not None:
            capture_path.parent.mkdir(parents=True, exist_ok=True)
            capture = capture_path.open("x", encoding="utf-8", newline="\n")
            capture.write(_canonical({
                "format": "quantarium-live-capture-v1",
                "source_feed_id": spec.feed_id,
                "start_at": spec.start_at.isoformat(),
                "stop_at": spec.stop_at.isoformat(),
                "market_feed": spec.market_feed,
                "symbols": sorted(feed.symbols),
                "finalization_delay_seconds": spec.finalization_delay_seconds,
            }) + "\n")
            capture.flush()
        initial_clock = _utc(clock())
        if capture is not None:
            _capture_event(capture, initial_clock, [])
        feed.flush(initial_clock)
        url = f"wss://stream.data.alpaca.markets/v2/{spec.market_feed}"
        async with connector(url, max_queue=16, max_size=1_048_576) as socket:
            stream_started = True
            await _await_control(socket, "connected")
            await socket.send(_canonical({"action": "auth", "key": key, "secret": secret}))
            await _await_control(socket, "authenticated")
            symbols = sorted(feed.symbols)
            await socket.send(_canonical({
                "action": "subscribe", "bars": symbols, "updatedBars": symbols,
            }))
            subscription = await _await_control(socket, "subscription")
            if (set(subscription.get("bars", [])) != feed.symbols
                    or set(subscription.get("updatedBars", [])) != feed.symbols):
                raise RuntimeError("Alpaca did not confirm every requested bar subscription")
            while not feed.finished:
                try:
                    frame = await asyncio.wait_for(socket.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    now = _utc(clock())
                    if capture is not None:
                        _capture_event(capture, now, [])
                    feed.flush(now)
                    continue
                observed_at = _utc(clock())
                items = _stream_items(frame)
                if capture is not None:
                    _capture_event(capture, observed_at, items)
                for item in items:
                    if item.get("T") == "error":
                        raise RuntimeError(
                            f"Alpaca stream error {item.get('code')}: {item.get('msg')}"
                        )
                    parsed = parse_alpaca_minute_event(item)
                    if parsed is not None:
                        bar, _ = parsed
                        feed.accept(bar, observed_at)
                feed.flush(observed_at)
        if not feed.finished:
            feed.store.stop("halted", "Alpaca stream disconnected before session end")
        return feed.report()
    except Exception as exc:
        if (stream_started and feed.last is not None and not feed.finished
                and not isinstance(exc, ConcurrentLiveRunnerError)):
            feed.store.stop("halted", f"Live stream interrupted: {type(exc).__name__}")
        raise
    finally:
        if capture is not None:
            capture.close()
        feed.close()


def run_live_replay(spec: LiveSpec, config_json: str, strategy_root: Path,
                    frames_path: Path) -> dict:
    """Replay captured incoming frames with their original receive timestamps."""
    if not frames_path.is_file():
        raise FileNotFoundError(frames_path)
    with frames_path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    with frames_path.open("r", encoding="utf-8-sig") as source:
        first_record = next(((number, line) for number, line in enumerate(source, start=1)
                             if line.strip()), None)
    if first_record is None:
        raise ValueError("Replay file is empty")
    try:
        first = json.loads(first_record[1])
    except ValueError as exc:
        raise ValueError("Invalid first replay line") from exc
    recorded_clock = isinstance(first, dict) and "format" in first
    if recorded_clock:
        if not is_format(first.get("format"), "live-capture"):
            raise ValueError("Unsupported Live Feed capture format")
        symbols = first.get("symbols")
        if (first.get("start_at") != spec.start_at.isoformat()
                or first.get("stop_at") != spec.stop_at.isoformat()
                or first.get("market_feed") != spec.market_feed
                or not isinstance(symbols, list)
                or not all(isinstance(symbol, str) for symbol in symbols)
                or set(symbols) != {
                    symbol for cell in spec.cells for symbol in cell.symbols
                }
                or first.get("finalization_delay_seconds")
                != spec.finalization_delay_seconds):
            raise ValueError("Replay config does not match captured market session")
    replay_spec = replace(spec, input_kind="replay", input_sha256=digest)
    feed = LiveFeed(replay_spec, config_json, strategy_root)
    try:
        with frames_path.open("r", encoding="utf-8-sig") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    continue
                if recorded_clock and line_number == first_record[0]:
                    continue
                try:
                    frame = json.loads(line)
                    timestamp = frame["received_at"]
                    if not isinstance(timestamp, str):
                        raise ValueError("received_at must be a timestamp string")
                    received_at = _read_timestamp(timestamp)
                    items = frame["items"]
                    if not isinstance(items, list) or not all(
                        isinstance(item, dict) for item in items
                    ):
                        raise ValueError("items must be an array of objects")
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(f"Invalid replay frame on line {line_number}") from exc
                if feed.last_clock is not None and received_at < feed.last_clock:
                    raise ValueError(f"Replay frame times are out of order on line {line_number}")
                while not recorded_clock and not feed.finished:
                    expected = (replay_spec.start_at if feed.last is None
                                else feed.last + MINUTE)
                    if expected >= replay_spec.stop_at:
                        break
                    due = expected + timedelta(
                        seconds=replay_spec.finalization_delay_seconds
                    )
                    if due >= received_at:
                        break
                    feed.flush(due)
                for item in items:
                    if item.get("T") == "error":
                        if feed.last is not None:
                            feed.store.stop("halted", "Captured stream error")
                        raise ValueError(f"Captured stream error on line {line_number}")
                    parsed = parse_alpaca_minute_event(item)
                    if parsed is not None:
                        bar, _ = parsed
                        feed.accept(bar, received_at)
                feed.flush(received_at)
        while not recorded_clock and not feed.finished:
            expected = replay_spec.start_at if feed.last is None else feed.last + MINUTE
            due = expected + timedelta(seconds=replay_spec.finalization_delay_seconds)
            feed.flush(due)
        with frames_path.open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != digest:
                feed.store.invalidate_replay("Replay input changed while being read")
                raise ValueError("Replay input changed while being read; use a new feed_id")
        return feed.report()
    finally:
        feed.close()
