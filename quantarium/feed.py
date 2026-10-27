"""Incremental Feed batch runner with immutable first-seen bars."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from .actions import normalize_feed_actions
from .data import validate_bar
from .engine import CellRuntime
from .errors import QuantariumError
from .model import Bar, CellSpec, CorporateAction, FeedSpec
from .provider import NEW_YORK


class DailyProvider(Protocol):
    def sessions(self, start: date, through: date) -> set[date]: ...
    def fetch(self, symbols: set[str], start: date, through: date) -> dict[date, dict[str, Bar]]: ...
    def actions(self, symbols: set[str], start: date, through: date) -> list[dict]: ...


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _bar_json(bar: Bar) -> str:
    return _canonical({
        "date": bar.date.isoformat(), "symbol": bar.symbol, "open": bar.open,
        "high": bar.high, "low": bar.low, "close": bar.close, "volume": bar.volume,
    })


def _settings_sha256(config_json: str) -> str:
    """Experiment-wide settings: the whole config except its cells."""
    settings = json.loads(config_json)
    if isinstance(settings, dict):
        settings.pop("cells", None)
    return hashlib.sha256(_canonical(settings).encode()).hexdigest()


def _cell_sha256(cell: CellSpec) -> str:
    pinned = {
        "name": cell.name, "mode": cell.mode, "symbols": list(cell.symbols),
        "initial_cash": cell.initial_cash,
        "strategies": [{"entrypoint": item.entrypoint, "allocation": item.allocation,
                        "parameters": dict(item.parameters)} for item in cell.strategies],
        "rebalance_every_sessions": cell.rebalance_every_sessions,
        "max_position_weight": cell.max_position_weight,
        "min_cash_weight": cell.min_cash_weight,
        "max_previous_volume_participation": cell.max_previous_volume_participation,
    }
    # Omit unused new fields so existing Feed experiments retain their hash.
    if cell.initial_positions:
        pinned["initial_positions"] = {
            symbol: {"shares": shares, "cost": cost}
            for symbol, (shares, cost) in sorted(cell.initial_positions.items())
        }
    if cell.information_end is not None:
        pinned["information_end"] = cell.information_end.isoformat()
    return hashlib.sha256(_canonical(pinned).encode()).hexdigest()


class FeedStore:
    def __init__(self, spec: FeedSpec, config_json: str, hashes: dict[str, list[str | None]]):
        path = Path(spec.database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self.spec = spec
        self.sequences: dict[str, int] = {}
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS feed_experiments (
                id TEXT PRIMARY KEY,
                config_sha256 TEXT NOT NULL,
                created_utc TEXT NOT NULL,
                provider TEXT NOT NULL,
                market_feed TEXT NOT NULL,
                start_date TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS feed_cells (
                feed_id TEXT NOT NULL REFERENCES feed_experiments(id),
                name TEXT NOT NULL,
                mode TEXT NOT NULL,
                initial_cash REAL NOT NULL,
                source_hashes_json TEXT NOT NULL,
                state_json TEXT NOT NULL,
                sequence INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (feed_id, name)
            );
            CREATE TABLE IF NOT EXISTS feed_bars (
                feed_id TEXT NOT NULL REFERENCES feed_experiments(id),
                date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume INTEGER NOT NULL,
                first_seen_utc TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                PRIMARY KEY (feed_id, date, symbol)
            );
            CREATE TABLE IF NOT EXISTS feed_revisions (
                feed_id TEXT NOT NULL,
                date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                detected_utc TEXT NOT NULL,
                original_sha256 TEXT NOT NULL,
                revised_sha256 TEXT NOT NULL,
                revised_bar_json TEXT NOT NULL,
                PRIMARY KEY (feed_id, date, symbol, revised_sha256)
            );
            CREATE TABLE IF NOT EXISTS feed_actions (
                feed_id TEXT NOT NULL REFERENCES feed_experiments(id),
                id TEXT NOT NULL,
                terms_json TEXT NOT NULL,
                PRIMARY KEY (feed_id, id)
            );
            CREATE TABLE IF NOT EXISTS feed_events (
                feed_id TEXT NOT NULL,
                cell_name TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                date TEXT NOT NULL,
                kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (feed_id, cell_name, sequence),
                FOREIGN KEY (feed_id, cell_name) REFERENCES feed_cells(feed_id, name)
            );
            CREATE INDEX IF NOT EXISTS feed_events_kind_date ON feed_events(feed_id, kind, date);
            """
        )
        cell_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(feed_cells)")}
        for column, ddl in (
            ("status", "status TEXT NOT NULL DEFAULT 'active'"),
            ("error", "error TEXT"),
            ("spec_sha256", "spec_sha256 TEXT"),
            ("joined_after", "joined_after TEXT"),
            # When the cell's configuration was frozen; sessions that closed
            # earlier are a historical backfill, later ones a forward test.
            ("created_utc", "created_utc TEXT"),
        ):
            if column not in cell_columns:
                self.connection.execute(f"ALTER TABLE feed_cells ADD COLUMN {ddl}")
        experiment_columns = {row[1] for row in self.connection.execute(
            "PRAGMA table_info(feed_experiments)"
        )}
        if "settings_sha256" not in experiment_columns:
            self.connection.execute("ALTER TABLE feed_experiments ADD COLUMN settings_sha256 TEXT")
        self.connection.commit()
        config_hash = hashlib.sha256(_canonical(json.loads(config_json)).encode()).hexdigest()
        settings_hash = _settings_sha256(config_json)
        cell_hashes = {cell.name: _cell_sha256(cell) for cell in spec.cells}
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            existing = self.connection.execute(
                "SELECT config_sha256, settings_sha256 FROM feed_experiments WHERE id = ?",
                (spec.feed_id,),
            ).fetchone()
            if existing is None:
                self.connection.execute(
                    "INSERT INTO feed_experiments(id, config_sha256, created_utc, provider, "
                    "market_feed, start_date, settings_sha256) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (spec.feed_id, config_hash, datetime.now(timezone.utc).isoformat(),
                     spec.provider, spec.market_feed, spec.start_date.isoformat(), settings_hash),
                )
                for cell in spec.cells:
                    self._insert_cell(cell, hashes[cell.name], cell_hashes[cell.name], None)
            elif existing[1] is None:
                # Experiments created before per-cell pinning can only resume
                # with the identical config; doing so records the new hashes.
                saved_names = {row[0] for row in self.connection.execute(
                    "SELECT name FROM feed_cells WHERE feed_id=?", (spec.feed_id,),
                )}
                if existing[0] != config_hash or saved_names != set(cell_hashes):
                    raise ValueError(
                        "Feed configuration changed; use a new feed_id for a new experiment. "
                        "Run this experiment once with its original config before adding cells"
                    )
                self.connection.execute(
                    "UPDATE feed_experiments SET settings_sha256=? WHERE id=?",
                    (settings_hash, spec.feed_id),
                )
                for cell in spec.cells:
                    self.connection.execute(
                        "UPDATE feed_cells SET spec_sha256=? WHERE feed_id=? AND name=?",
                        (cell_hashes[cell.name], spec.feed_id, cell.name),
                    )
            else:
                if existing[1] != settings_hash:
                    raise QuantariumError(
                        "Feed configuration changed outside its cells; use a new feed_id for a new experiment",
                        "feed_settings_changed", "Deploy again with a new feed_id",
                    )
                saved = dict(self.connection.execute(
                    "SELECT name, spec_sha256 FROM feed_cells WHERE feed_id=?", (spec.feed_id,),
                ))
                removed = sorted(set(saved) - set(cell_hashes))
                if removed:
                    raise ValueError(
                        f"Cells cannot be removed from a Feed experiment: {', '.join(removed)}. "
                        "Use retire-cell to stop a cell"
                    )
                for name, digest in saved.items():
                    if digest != cell_hashes[name]:
                        raise ValueError(
                            f"Cell configuration changed in {name}; add a cell with a new name "
                            "or use a new feed_id"
                        )
                added = [cell for cell in spec.cells if cell.name not in saved]
                if added:
                    self._join_cells(added, hashes, cell_hashes)
                self.connection.execute(
                    "UPDATE feed_experiments SET config_sha256=? WHERE id=?",
                    (config_hash, spec.feed_id),
                )
            for cell in spec.cells:
                row = self.connection.execute(
                    "SELECT source_hashes_json FROM feed_cells WHERE feed_id = ? AND name = ?",
                    (spec.feed_id, cell.name),
                ).fetchone()
                if row is None or json.loads(row[0]) != hashes[cell.name]:
                    raise ValueError(f"Strategy source changed in {cell.name}; use a new feed_id")
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            self.close()
            raise
        self.refresh_sequences()

    def _insert_cell(self, cell: CellSpec, source_hashes: list[str | None], spec_hash: str,
                     joined_after: date | None) -> None:
        state = {
            "cash": cell.initial_cash,
            "positions": {s: 0 for s in cell.symbols},
            "pending": None,
            "last_date": joined_after.isoformat() if joined_after else None,
            "equity": cell.initial_cash,
            "strategy_states": [None for _ in cell.strategies],
        }
        self.connection.execute(
            "INSERT INTO feed_cells(feed_id, name, mode, initial_cash, source_hashes_json, "
            "state_json, spec_sha256, joined_after, created_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.spec.feed_id, cell.name, cell.mode, cell.initial_cash,
             _canonical(source_hashes), _canonical(state), spec_hash,
             joined_after.isoformat() if joined_after else None,
             datetime.now(timezone.utc).isoformat()),
        )

    def _join_cells(self, cells: list[CellSpec], hashes: dict[str, list[str | None]],
                    cell_hashes: dict[str, str]) -> None:
        """Add cells that start after the last processed session."""
        last = self.last_date()
        days = self.connection.execute(
            "SELECT COUNT(DISTINCT date) FROM feed_bars WHERE feed_id=?", (self.spec.feed_id,),
        ).fetchone()[0]
        for cell in cells:
            for symbol in cell.symbols:
                saved = self.connection.execute(
                    "SELECT COUNT(*) FROM feed_bars WHERE feed_id=? AND symbol=?",
                    (self.spec.feed_id, symbol),
                ).fetchone()[0]
                if saved not in {0, days}:
                    raise ValueError(
                        f"Cannot add {cell.name}: saved history for {symbol} has gaps because "
                        "the cells that held it stopped. Use a new feed_id"
                    )
            self._insert_cell(cell, hashes[cell.name], cell_hashes[cell.name], last)

    def refresh_sequences(self) -> None:
        """Reload event counters, which retire-cell may advance from another process."""
        self.sequences = dict(self.connection.execute(
            "SELECT name, sequence FROM feed_cells WHERE feed_id=?", (self.spec.feed_id,),
        ))

    def active_cells(self) -> set[str]:
        return {row[0] for row in self.connection.execute(
            "SELECT name FROM feed_cells WHERE feed_id=? AND status='active'",
            (self.spec.feed_id,),
        )}

    def close(self) -> None:
        self.connection.close()

    def state(self, cell_name: str) -> dict:
        row = self.connection.execute(
            "SELECT state_json FROM feed_cells WHERE feed_id = ? AND name = ?",
            (self.spec.feed_id, cell_name),
        ).fetchone()
        if row is None:
            raise ValueError(f"Cell not found in feed experiment: {cell_name}")
        return json.loads(row[0])

    def last_date(self) -> date | None:
        """The last processed session, which every active cell must have reached."""
        value = self.connection.execute(
            "SELECT MAX(date) FROM feed_bars WHERE feed_id=?", (self.spec.feed_id,),
        ).fetchone()[0]
        dates = {self.state(name)["last_date"] for name in self.active_cells()}
        if dates - {value}:
            raise ValueError("Feed cells have inconsistent checkpoints")
        return date.fromisoformat(value) if value else None

    def symbols_on(self, day: date | None) -> set[str]:
        if day is None:
            return set()
        return {row[0] for row in self.connection.execute(
            "SELECT symbol FROM feed_bars WHERE feed_id=? AND date=?",
            (self.spec.feed_id, day.isoformat()),
        )}

    def history(self, symbols: tuple[str, ...], through: date | None) -> dict[str, list[Bar]]:
        result: dict[str, list[Bar]] = {s: [] for s in symbols}
        if through is None:
            return result
        rows = self.connection.execute(
            "SELECT date, symbol, open, high, low, close, volume FROM feed_bars "
            "WHERE feed_id = ? AND date <= ? ORDER BY date, symbol",
            (self.spec.feed_id, through.isoformat()),
        )
        for row in rows:
            if row[1] in result:
                result[row[1]].append(Bar(date.fromisoformat(row[0]), row[1], *row[2:]))
        return result

    def compare_overlapping_bars(self, bars: dict[date, dict[str, Bar]], through: date | None) -> int:
        if through is None:
            return 0
        revisions = 0
        now = datetime.now(timezone.utc).isoformat()
        for day, by_symbol in bars.items():
            if day > through:
                continue
            for symbol, bar in by_symbol.items():
                row = self.connection.execute(
                    "SELECT sha256 FROM feed_bars WHERE feed_id=? AND date=? AND symbol=?",
                    (self.spec.feed_id, day.isoformat(), symbol),
                ).fetchone()
                if row is None:
                    raise ValueError(f"Previously processed bar missing from cache: {symbol} {day}")
                body = _bar_json(bar)
                digest = hashlib.sha256(body.encode()).hexdigest()
                if digest != row[0]:
                    self.connection.execute(
                        "INSERT OR IGNORE INTO feed_revisions VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (self.spec.feed_id, day.isoformat(), symbol, now, row[0], digest, body),
                    )
                    revisions += 1
        self.connection.commit()
        return revisions

    @staticmethod
    def _action_terms(action: CorporateAction) -> str:
        return _canonical({
            "id": action.source_id, "kind": action.kind, "symbol": action.symbol,
            "date": action.date.isoformat(), "ex_date": action.ex_date.isoformat(),
            "value": action.value,
            "process_date": action.process_date.isoformat() if action.process_date else None,
        })

    def validate_actions(self, actions: list[CorporateAction], last: date | None) -> None:
        current = {action.source_id: self._action_terms(action) for action in actions}
        saved = dict(self.connection.execute(
            "SELECT id, terms_json FROM feed_actions WHERE feed_id=?", (self.spec.feed_id,),
        ))
        for source_id, terms in saved.items():
            if source_id not in current:
                raise ValueError(f"Previously pinned corporate action disappeared: {source_id}")
            if current[source_id] != terms:
                raise ValueError(f"Previously pinned corporate action changed: {source_id}")
        if last is not None:
            for action in actions:
                if action.source_id not in saved and action.ex_date <= last:
                    raise ValueError(
                        f"Late corporate action {action.source_id} affects {action.ex_date}; "
                        "prior Feed valuations may be inaccurate"
                    )

    def save_actions(self, actions: list[CorporateAction], expected_last: date | None) -> None:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            if self.last_date() != expected_last:
                raise RuntimeError("Another process advanced this feed; retry the command")
            self.validate_actions(actions, expected_last)
            saved_ids = {row[0] for row in self.connection.execute(
                "SELECT id FROM feed_actions WHERE feed_id=?", (self.spec.feed_id,),
            )}
            for action in actions:
                if action.source_id not in saved_ids:
                    self.connection.execute(
                        "INSERT INTO feed_actions VALUES (?, ?, ?)",
                        (self.spec.feed_id, action.source_id, self._action_terms(action)),
                    )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def event(self, cell_name: str, day: str, kind: str, payload: object) -> None:
        self.sequences[cell_name] += 1
        self.connection.execute(
            "INSERT INTO feed_events VALUES (?, ?, ?, ?, ?, ?)",
            (self.spec.feed_id, cell_name, self.sequences[cell_name], day, kind, _canonical(payload)),
        )

    def process_day(
        self, day: date, bars: dict[str, Bar], runtimes: dict[str, CellRuntime],
        actions: tuple[CorporateAction, ...] = (),
        *, shared_history: dict[str, list[Bar]],
    ) -> set[str]:
        previous = self.last_date()
        if previous is not None and day <= previous:
            raise ValueError(f"Feed day {day} has already been processed")
        now = datetime.now(timezone.utc).isoformat()
        appended: list[str] = []
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            if self.last_date() != previous:
                raise RuntimeError("Another process advanced this feed; retry the command")
            for symbol, bar in bars.items():
                digest = hashlib.sha256(_bar_json(bar).encode()).hexdigest()
                self.connection.execute(
                    "INSERT INTO feed_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (self.spec.feed_id, day.isoformat(), symbol, bar.open, bar.high,
                     bar.low, bar.close, bar.volume, now, digest),
                )
            self.refresh_sequences()
            active = self.active_cells()
            failed: set[str] = set()
            for cell in self.spec.cells:
                if cell.name not in active:
                    continue
                runtime = runtimes[cell.name]
                prior_sequence = self.sequences[cell.name]
                self.connection.execute("SAVEPOINT feed_cell")
                try:
                    if prior_sequence == 0:
                        runtime.strategy_events(self, day)
                    cell_bars = {s: bars[s] for s in cell.symbols}
                    cell_actions = tuple(action for action in actions
                                         if action.symbol in cell.symbols)
                    if cell_actions:
                        runtime.process_day(day, cell_bars, self, None, cell_actions)
                    else:
                        runtime.process_day(day, cell_bars, self, None)
                    state_json = _canonical(runtime.checkpoint())
                    self.connection.execute(
                        "UPDATE feed_cells SET state_json=?, sequence=? WHERE feed_id=? AND name=?",
                        (state_json, self.sequences[cell.name], self.spec.feed_id, cell.name),
                    )
                except Exception as exc:
                    if isinstance(exc, sqlite3.Error):
                        raise
                    # Discard this cell's partial day. It keeps its checkpoint
                    # from the previous session and takes no further sessions.
                    self.connection.execute("ROLLBACK TO SAVEPOINT feed_cell")
                    self.connection.execute("RELEASE SAVEPOINT feed_cell")
                    self.sequences[cell.name] = prior_sequence
                    reason = f"{type(exc).__name__}: {exc}"
                    self.event(cell.name, day.isoformat(), "cell_failed", {"reason": reason})
                    self.connection.execute(
                        "UPDATE feed_cells SET status='failed', error=?, sequence=? "
                        "WHERE feed_id=? AND name=?",
                        (reason, self.sequences[cell.name], self.spec.feed_id, cell.name),
                    )
                    failed.add(cell.name)
                else:
                    self.connection.execute("RELEASE SAVEPOINT feed_cell")
            for symbol, bar in bars.items():
                shared_history[symbol].append(bar)
                appended.append(symbol)
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            for symbol in appended:
                shared_history[symbol].pop()
            raise
        return failed

    def report(self) -> dict:
        return _report(self.connection, self.spec.feed_id)


def _report(connection: sqlite3.Connection, feed_id: str) -> dict:
    row = connection.execute(
        "SELECT created_utc, provider, market_feed, start_date FROM feed_experiments WHERE id=?",
        (feed_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Feed experiment not found: {feed_id}")
    cells = []
    columns = {column[1] for column in connection.execute("PRAGMA table_info(feed_cells)")}
    status = "status" if "status" in columns else "'active'"
    error = "error" if "error" in columns else "NULL"
    joined = "joined_after" if "joined_after" in columns else "NULL"
    for name, mode, initial_cash, state_json, cell_status, cell_error, joined_after in connection.execute(
        f"SELECT name, mode, initial_cash, state_json, {status}, {error}, {joined} "
        "FROM feed_cells WHERE feed_id=? ORDER BY name",
        (feed_id,),
    ):
        state = json.loads(state_json)
        cells.append({
            "name": name, "mode": mode, "last_date": state["last_date"],
            "initial_cash": initial_cash, "cash": state["cash"],
            "positions": state["positions"], "equity": state["equity"],
            "return_pct": 100 * (state["equity"] / initial_cash - 1),
            "status": cell_status, "error": cell_error, "joined_after": joined_after,
        })
    bar_count = connection.execute(
        "SELECT COUNT(*) FROM feed_bars WHERE feed_id=?", (feed_id,)
    ).fetchone()[0]
    revision_count = connection.execute(
        "SELECT COUNT(*) FROM feed_revisions WHERE feed_id=?", (feed_id,)
    ).fetchone()[0]
    action_table_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='feed_actions'"
    ).fetchone() is not None
    action_count = (connection.execute(
        "SELECT COUNT(*) FROM feed_actions WHERE feed_id=?", (feed_id,),
    ).fetchone()[0] if action_table_exists else 0)
    return {
        "feed_id": feed_id, "created_utc": row[0],
        "provider": row[1], "market_feed": row[2], "start_date": row[3],
        "bars_cached": bar_count, "revisions_observed": revision_count,
        "actions_pinned": action_count, "cells": cells,
    }


def read_feed_report(path: Path, feed_id: str) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        return _report(connection, feed_id)
    finally:
        connection.close()


def provider_for(spec: FeedSpec) -> DailyProvider:
    """The daily-bar source a Feed config names."""
    if spec.provider == "scenario":
        from .scenario import ScenarioProvider, load_scenario
        return ScenarioProvider(load_scenario(Path(spec.scenario_path)))
    if spec.provider == "csv":
        from .importer import CsvFeedProvider
        return CsvFeedProvider(Path(spec.data_path))
    from .provider import AlpacaDailyProvider
    return AlpacaDailyProvider.from_environment(spec.market_feed)


def safe_through_date(now: datetime | None = None) -> date:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("Current time must include a timezone")
    return current.astimezone(NEW_YORK).date() - timedelta(days=1)


def run_feed_batch(
    spec: FeedSpec,
    config_json: str,
    strategy_root: Path,
    provider: DailyProvider,
    *,
    through: date | None = None,
    now: datetime | None = None,
) -> dict:
    if spec.ignore_corporate_actions and spec.apply_supported_corporate_actions:
        raise ValueError("Feed cannot apply and ignore corporate actions together")
    if spec.provider == "scenario":
        # A scenario is already complete; --through decides how far it is
        # released, and its dates may lie in the future.
        end = through or max(provider.sessions(spec.start_date, date.max),
                             default=spec.start_date)
    else:
        safe_date = safe_through_date(now)
        end = through or safe_date
        if end > safe_date:
            raise ValueError(
                f"Feed batch accepts only dates through {safe_date} (New York date minus one)"
            )
    runtime_spec = spec.runtime_spec()
    runtimes = {cell.name: CellRuntime(cell, runtime_spec, strategy_root,
                                      append_history=False) for cell in spec.cells}
    hashes = {name: runtime.source_hashes for name, runtime in runtimes.items()}
    if any(value is None for values in hashes.values() for value in values):
        raise ValueError("Feed strategies must have source files so their versions can be pinned")
    store = FeedStore(spec, config_json, hashes)
    try:
        last = store.last_date()
        active = store.active_cells()
        if not active:
            return store.report() | {"days_processed_this_call": 0, "active_cells": 0}
        active_specs = [cell for cell in spec.cells if cell.name in active]
        # Only active cells' symbols are fetched, so a delisted symbol held by a
        # retired or failed cell cannot stop the rest of the experiment.
        symbols = {symbol for cell in active_specs for symbol in cell.symbols}
        shared_history = store.history(tuple(sorted(symbols)), last)
        for cell in active_specs:
            runtimes[cell.name].restore_checkpoint(
                store.state(cell.name), {symbol: shared_history[symbol]
                                         for symbol in cell.symbols}
            )
        start = last or spec.start_date
        if start > end:
            return store.report() | {"days_processed_this_call": 0}
        actions: list[CorporateAction] = []
        if not spec.ignore_corporate_actions:
            lookback = date.fromordinal(max(1, spec.start_date.toordinal() - 366))
            reported = provider.actions(symbols, lookback, end)
            if spec.apply_supported_corporate_actions:
                actions = normalize_feed_actions(reported, spec.start_date, end)
                store.validate_actions(actions, last)
            else:
                relevant = [item for item in reported
                            if not isinstance(item.get("ex_date"), str)
                            or date.fromisoformat(item["ex_date"]) >= spec.start_date]
                if relevant:
                    first = sorted(relevant, key=lambda action: action["process_date"])[0]
                    raise ValueError(
                        f"Unmodeled corporate action for {first['symbol']} on "
                        f"{first['process_date']} ({first['type']}); Feed batch stopped before new sessions. "
                        "Use a new feed experiment with ignore_corporate_actions=true only if this limitation is acceptable"
                    )
        sessions = provider.sessions(start, end)
        if any(not start <= day <= end for day in sessions):
            raise ValueError("Provider returned an out-of-range market session")
        fetched = provider.fetch(symbols, start, end) if sessions else {}
        for day, bars in fetched.items():
            if not start <= day <= end or set(bars) - symbols:
                raise ValueError("Provider returned unexpected dates or symbols")
            for symbol, bar in bars.items():
                if bar.date != day or bar.symbol != symbol:
                    raise ValueError("Provider returned a mismatched daily bar")
                validate_bar(bar)
        unexpected = set(fetched) - sessions
        if unexpected:
            raise ValueError(f"Bars returned on non-session dates: {', '.join(map(str, sorted(unexpected)))}")
        missing_days = sessions - set(fetched)
        if missing_days:
            raise ValueError(f"Missing complete market sessions: {', '.join(map(str, sorted(missing_days)))}")
        for day in sorted(sessions):
            missing_symbols = symbols - set(fetched[day])
            if missing_symbols:
                raise ValueError(
                    f"Incomplete feed on {day}: missing {', '.join(sorted(missing_symbols))}. "
                    "The symbol may be halted, delisted, not yet listed, or absent from this feed"
                )
        if actions:
            for action in actions:
                if action.ex_date >= start and action.ex_date not in sessions:
                    raise ValueError(f"Corporate-action ex-date is not a new market session: {action.ex_date}")
                if start <= action.date <= end and action.date not in sessions:
                    raise ValueError(f"Corporate-action payment date is not a market session: {action.date}")
            by_day: dict[date, list[CorporateAction]] = {}
            for action in actions:
                for event_day in {action.ex_date, action.date}:
                    if start <= event_day <= end:
                        by_day.setdefault(event_day, []).append(action)
        else:
            by_day = {}
        saved_on_last = store.symbols_on(last) & symbols
        if last is not None and not saved_on_last <= set(fetched.get(last, {})):
            raise ValueError(f"Provider did not return the complete last processed day: {last}")
        # Symbols first requested by a newly added cell have no saved bars to
        # compare; their history starts with the next processed session.
        store.compare_overlapping_bars(
            {day: {symbol: bar for symbol, bar in by_symbol.items()
                   if symbol in saved_on_last or day > last}
             for day, by_symbol in fetched.items()} if last else fetched,
            last,
        )
        if spec.apply_supported_corporate_actions:
            store.save_actions(actions, last)
        new_days = sorted(day for day in sessions if last is None or day > last)
        processed = 0
        for day in new_days:
            store.process_day(day, fetched[day], runtimes, tuple(by_day.get(day, ())),
                              shared_history=shared_history)
            processed += 1
            if not store.active_cells():
                break
        return store.report() | {"days_processed_this_call": processed}
    finally:
        store.close()

