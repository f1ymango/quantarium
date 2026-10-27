"""Daily static replay using close-time signals and next-open fills."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import inspect
import json
import math
import sqlite3
import sys
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .data import load_actions_csv, load_daily_csv
from .journal import Journal
from .legacy import current_entrypoint, current_value
from .userdata import Delisting, check_delistings, parse_delistings, parse_membership, symbol_spans
from .provenance import install_import_guard, refuse_unreviewed, unreviewed_entry
from .model import (ORDER_KINDS, ORDER_SIDES, Bar, BarHistory, CellSpec, Context, CorporateAction, CostModel, Decision, Sleeve,
                    Order, RunSpec)

# Imports anywhere in this process refuse unreviewed agent-written files, so a
# reviewed script cannot bring one in by importing it.
install_import_guard()


@contextmanager
def _importable(root: Path):
    """Put a strategy's folder on the import path while it loads, then take it off."""
    entry = str(root)
    added = entry not in sys.path
    if added:
        sys.path.insert(0, entry)
    try:
        yield
    finally:
        if added:
            try:
                sys.path.remove(entry)
            except ValueError:
                pass


def _load_strategy(entrypoint: str, parameters: dict[str, Any], root: Path, *,
                   sandbox_unreviewed: bool = False, method: str = "on_bar") -> tuple[object, str | None]:
    """Load a strategy class and instantiate it.

    Agent-written scripts that the user has not reviewed never run in this
    process. With ``sandbox_unreviewed`` (Static simulations only) they run in
    the sandbox; otherwise loading them is refused.
    """
    # Configs and Lab records written before 1.0 name the pre-1.0 modules and hidden folder.
    entrypoint, parameters = current_entrypoint(entrypoint), current_value(parameters)
    if ":" not in entrypoint:
        raise ValueError(f"Strategy entrypoint must be module:Class: {entrypoint}")
    module_name, class_name = entrypoint.split(":", 1)
    root = root.resolve()
    # The script's folder is importable only while it loads, so agent files
    # never stay on the server's import path.
    with _importable(root):
        local_file = root / f"{module_name}.py" if module_name.isidentifier() else None
        if local_file is not None and local_file.is_file():
            source_bytes = local_file.read_bytes()
            digest = hashlib.sha256(source_bytes).hexdigest()
            if unreviewed_entry(local_file, source_bytes) is not None:
                if not sandbox_unreviewed:
                    refuse_unreviewed(local_file, source_bytes)
                from .sandbox import SandboxedStrategy

                return SandboxedStrategy(source_bytes, class_name, parameters, local_file.name), digest
            namespace = hashlib.sha256(str(local_file).encode()).hexdigest()[:16]
            cache_key = f"_quantarium_strategy_{namespace}_{digest}"
            module = sys.modules.get(cache_key)
            if module is None:
                module_spec = importlib.util.spec_from_file_location(cache_key, local_file)
                if module_spec is None:
                    raise ImportError(f"Cannot load strategy module: {local_file}")
                module = importlib.util.module_from_spec(module_spec)
                sys.modules[cache_key] = module
                try:
                    exec(compile(source_bytes, str(local_file), "exec"), module.__dict__)
                except BaseException:
                    del sys.modules[cache_key]
                    raise
            strategy_class = getattr(module, class_name)
        else:
            # Check every file this import could run before running any of it.
            parts = module_name.split(".")
            for base in [root, *(Path(entry) for entry in sys.path if entry)]:
                for depth in range(1, len(parts) + 1):
                    stem = base.joinpath(*parts[:depth])
                    for candidate in (stem.with_suffix(".py"), stem / "__init__.py"):
                        if candidate.is_file():
                            refuse_unreviewed(candidate, candidate.read_bytes())
            strategy_class = getattr(importlib.import_module(module_name), class_name)
            source = inspect.getsourcefile(strategy_class)
            digest = hashlib.sha256(Path(source).read_bytes()).hexdigest() if source else None
        strategy = strategy_class(**parameters)
        if not callable(getattr(strategy, method, None)):
            raise ValueError(f"{entrypoint} needs an {method}(context{', sleeves' if method == 'allocate' else ''}) method")
        return strategy, digest


class AskAblated(ValueError):
    """Raised by ``context.ask`` in a Lab variant whose language model is switched off."""


def _validate_weights(raw: object, cell: CellSpec) -> dict[str, float]:
    if isinstance(raw, Decision):
        raise ValueError(f"Strategy in {cell.name} returned a Decision, which Static and Feed Batch "
                         "support but Live Feed does not yet")
    if cell.mode == "simple":
        if isinstance(raw, dict):
            raise ValueError(f"Strategy in {cell.name} is in a Simple cell, so it returns one target weight "
                             "from 0 to 1, not a dict of weights")
        raw = {cell.symbols[0]: raw}
    if not isinstance(raw, dict):
        raise ValueError(f"Strategy in {cell.name} must return target weights")
    if set(raw) - set(cell.symbols):
        raise ValueError(f"Strategy in {cell.name} targeted symbols outside the cell")
    weights: dict[str, float] = {}
    for symbol, value in raw.items():
        weight = float(value)
        if not math.isfinite(weight) or weight < 0 or weight > 1:
            raise ValueError(f"Invalid target weight for {symbol} in {cell.name}")
        weights[symbol] = weight
    if sum(weights.values()) > 1 + 1e-12:
        raise ValueError(f"Strategy weights exceed 100% in {cell.name}")
    return weights


@dataclass(frozen=True)
class Universe:
    """Symbols that list, delist, and change index membership during a Static run (ADR-0039)."""

    spans: Mapping[str, tuple[date, date]]
    delistings: Mapping[str, Delisting]
    membership: Mapping[str, Mapping[str, tuple[tuple[date, date | None], ...]]]

    def member(self, index: str, symbol: str, day: date) -> bool:
        return any(start <= day <= (through or date.max)
                   for start, through in self.membership[index].get(symbol, ()))


def _universe(spec: RunSpec, days: list[date], tape: dict[date, dict[str, Bar]], symbols: set[str]) -> Universe:
    rename = spec.symbol_map or {}
    spans = symbol_spans(days, tape)
    missing = sorted(symbols - set(spans))
    if missing:
        raise ValueError(f"No prices for {', '.join(missing)}")
    delistings: dict[str, Delisting] = {}
    if spec.delistings_path:
        for symbol, delisting in parse_delistings(Path(spec.delistings_path)).items():
            symbol = rename.get(symbol, symbol)
            if symbol in spans:
                delistings[symbol] = delisting
        check_delistings(delistings, spans)
    membership: dict[str, dict[str, tuple]] = {}
    if spec.membership_path:
        for index, members in parse_membership(Path(spec.membership_path)).items():
            membership[index] = {rename.get(symbol, symbol): tuple(periods) for symbol, periods in members.items()}
    for cell in spec.cells:
        if cell.index is not None and cell.index not in membership:
            raise ValueError(f"{cell.name} holds the members of {cell.index}, but the data has no membership for it")
    return Universe(MappingProxyType(spans), MappingProxyType(delistings), MappingProxyType(membership))


def _universe_tape(path: Path, symbols: set[str], symbol_map: Mapping[str, str] | None,
                   ) -> tuple[list[date], dict[date, dict[str, Bar]], str]:
    """The run's bars on the whole file's calendar.

    The sessions come from every symbol in the file, not only the run's: a
    cell whose symbols all delist must still reach the session that pays
    them out, rather than end with them valued at their last close.
    """
    days, everything, digest = load_daily_csv(path, None, every_session=False)
    rename = symbol_map or {}
    tape: dict[date, dict[str, Bar]] = {}
    for day in days:
        today: dict[str, Bar] = {}
        for raw, bar in everything[day].items():
            name = rename.get(raw, raw)
            if name not in symbols:
                continue
            if name in today:
                raise ValueError(f"Duplicate bar for {name} on {day}")
            today[name] = bar if name == raw else replace(bar, symbol=name)
        tape[day] = today
    return days, tape, digest


def _commission(costs: CostModel, flat: float, shares: int) -> float:
    """What one filled order pays: the flat and per-share commissions, but at least the minimum."""
    return max(costs.commission_minimum, flat + costs.commission_per_share * shares)


def _slip(costs: CostModel, slippage_bps: float, previous: Bar | None) -> float:
    """The fraction a fill moves against the trader: flat, plus a share of the previous session's range."""
    extra = 0.0
    if costs.slippage_range_fraction and previous is not None and previous.close > 0:
        extra = costs.slippage_range_fraction * (previous.high - previous.low) / previous.close
    return slippage_bps / 10_000 + extra


def _affordable(cash: float, price: float, flat: float, costs: CostModel) -> int:
    """The most shares whose price and commission fit in ``cash``."""
    by_flat = math.floor((cash - flat + 1e-9) / (price + costs.commission_per_share))
    by_minimum = math.floor((cash - costs.commission_minimum + 1e-9) / price)
    return max(0, min(by_flat, by_minimum))


def _rebalance(
    cell: CellSpec,
    day: date,
    bars: dict[str, Bar],
    targets: dict[str, float],
    cash: float,
    positions: dict[str, int],
    slippage_bps: float,
    commission: float,
    journal: Any,
    previous_volumes: dict[str, int],
    untradable: frozenset[str] = frozenset(),
    fills: list | None = None,
    costs: CostModel = CostModel(),
    previous_bars: dict[str, Bar] | None = None,
) -> float:
    """Trade toward targets at each bar's open.

    Symbols in ``untradable`` had no trade in this interval: they are valued
    at their last close and their orders go unfilled. A symbol absent from
    ``bars`` has never traded, so it has no price and the cell cannot hold it.
    """
    marks = {s: bars[s].close if s in untradable else bars[s].open
             for s in cell.symbols if s in bars}
    equity_at_open = cash + sum(positions[s] * marks[s] for s in marks)
    desired = {s: math.floor(equity_at_open * targets.get(s, 0) / marks[s]) for s in marks}
    previous = previous_bars or {}
    volume_caps = (
        {symbol: math.floor(previous_volumes.get(symbol, 0) * cell.max_previous_volume_participation)
         for symbol in cell.symbols}
        if cell.max_previous_volume_participation is not None else {}
    )

    def unfilled(side: str, symbol: str, requested: int | None, reason: str) -> None:
        journal.event(cell.name, day.isoformat(), "order", {
            "side": side, "symbol": symbol, "requested_shares": requested,
            "filled_shares": 0, "fill_price": None, "commission": 0,
            "unfilled_reason": reason,
        })

    for symbol in sorted(cell.symbols):
        if symbol not in marks:
            continue
        requested = max(0, positions[symbol] - desired[symbol])
        if not requested:
            continue
        if symbol in untradable:
            unfilled("sell", symbol, requested, "no_trade_in_interval")
            continue
        price = bars[symbol].open * (1 - _slip(costs, slippage_bps, previous.get(symbol)))
        filled = min(requested, volume_caps.get(symbol, requested))
        fee = _commission(costs, commission, filled)
        if cash + filled * price < fee:
            filled = 0
        if filled:
            cash += filled * price - fee
            positions[symbol] -= filled
            if fills is not None:
                fills.append((symbol, "sell", filled, price))
        order = {
            "side": "sell", "symbol": symbol, "requested_shares": requested,
            "filled_shares": filled, "fill_price": price if filled else None,
            "commission": fee if filled else 0,
        }
        if volume_caps:
            order["previous_volume_cap_shares"] = volume_caps[symbol]
        journal.event(cell.name, day.isoformat(), "order", order)
    for symbol in sorted(cell.symbols):
        if symbol not in marks:
            if targets.get(symbol, 0) > 0:
                unfilled("buy", symbol, None, "no_price_observed")
            continue
        requested = max(0, desired[symbol] - positions[symbol])
        if not requested:
            continue
        if symbol in untradable:
            unfilled("buy", symbol, requested, "no_trade_in_interval")
            continue
        price = bars[symbol].open * (1 + _slip(costs, slippage_bps, previous.get(symbol)))
        filled = min(requested, _affordable(cash, price, commission, costs), volume_caps.get(symbol, requested))
        fee = _commission(costs, commission, filled)
        if filled:
            cash -= filled * price + fee
            positions[symbol] += filled
            if fills is not None:
                fills.append((symbol, "buy", filled, price))
        order = {
            "side": "buy", "symbol": symbol, "requested_shares": requested,
            "filled_shares": filled, "fill_price": price if filled else None,
            "commission": fee if filled else 0,
        }
        if volume_caps:
            order["previous_volume_cap_shares"] = volume_caps[symbol]
        journal.event(cell.name, day.isoformat(), "order", order)
    if cash < -1e-7:
        raise AssertionError("Rebalance produced negative cash")
    return max(0.0, cash)


def _validate_orders(orders: object, cell: CellSpec) -> list[Order]:
    if not isinstance(orders, (tuple, list)):
        raise ValueError(f"Decision.orders in {cell.name} must be a tuple of Order, or None")
    result = []
    for order in orders:
        if not isinstance(order, Order):
            raise ValueError(f"Decision.orders in {cell.name} must contain Order objects")
        if order.symbol not in cell.symbols:
            raise ValueError(f"Order for {order.symbol} is outside {cell.name}")
        if order.side not in ORDER_SIDES or order.kind not in ORDER_KINDS:
            raise ValueError(f"Order in {cell.name} needs side buy/sell and kind limit/stop")
        if isinstance(order.shares, bool) or not isinstance(order.shares, int) or order.shares <= 0:
            raise ValueError(f"Order shares in {cell.name} must be a positive whole number")
        if not math.isfinite(order.price) or order.price <= 0:
            raise ValueError(f"Order price in {cell.name} must be positive")
        sessions = order.expires_after_sessions
        if sessions is not None and (isinstance(sessions, bool) or not isinstance(sessions, int) or sessions < 1):
            raise ValueError(f"expires_after_sessions in {cell.name} must be a positive whole number")
        result.append(order)
    return result


def _trigger_price(order: dict, bar: Bar) -> float | None:
    """Where a resting order fills on a daily bar, or None if it does not.

    The order a gap jumps over fills at the open. Otherwise it fills at its
    own price when the day's range reaches it.
    """
    price = order["price"]
    if order["kind"] == "market":
        return bar.open
    if order["kind"] == "stop":
        if order["side"] == "sell":
            return bar.open if bar.open <= price else (price if bar.low <= price else None)
        return bar.open if bar.open >= price else (price if bar.high >= price else None)
    if order["side"] == "sell":
        return bar.open if bar.open >= price else (price if bar.high >= price else None)
    return bar.open if bar.open <= price else (price if bar.low <= price else None)


def _update_cost_basis(cost_basis: dict[str, float], positions: dict[str, int],
                       before: dict[str, int], fills: list) -> None:
    for symbol, side, shares, price in fills:
        if side == "buy":
            held = before[symbol]
            old = cost_basis.get(symbol, price) if held else price
            cost_basis[symbol] = (held * old + shares * price) / (held + shares)
            before[symbol] = held + shares
        else:
            before[symbol] -= shares
            if before[symbol] == 0:
                cost_basis.pop(symbol, None)
    for symbol in [s for s in cost_basis if positions.get(s, 0) == 0]:
        cost_basis.pop(symbol)


class CellRuntime:
    """One cell's account, strategies, and point-in-time history."""

    def __init__(
        self,
        cell: CellSpec,
        spec: RunSpec,
        strategy_root: Path,
        *,
        saved: dict[str, Any] | None = None,
        prior_history: dict[str, list[Bar]] | None = None,
        append_history: bool = True,
        sandbox_unreviewed: bool = False,
    ) -> None:
        participation = cell.max_previous_volume_participation
        if participation is not None and (
            not math.isfinite(participation) or participation < 0 or participation > 1
        ):
            raise ValueError(f"Invalid max_previous_volume_participation for {cell.name}")
        self.cell = cell
        self.spec = spec
        self.model_card = None
        self.information_end = cell.information_end
        if cell.model_card is not None:
            from .ml.card import load_card

            root = strategy_root.resolve()
            model_path = Path(cell.model_card)
            if not model_path.is_absolute():
                model_path = (root / model_path).resolve()
            if not Path(cell.model_card).is_absolute() and root not in model_path.parents:
                raise ValueError(f"{cell.name}.model_card leaves the strategy root")
            self.model_card = load_card(model_path)
            boundary = date.fromisoformat(self.model_card["information_end"])
            if self.information_end is not None and self.information_end != boundary:
                raise ValueError(f"{cell.name}.information_end differs from its model card")
            self.information_end = boundary
        self.cash = cell.initial_cash
        self.positions = {s: 0 for s in cell.symbols}
        self.pending: dict[str, float] | None = None
        self.last_date: date | None = None
        self.equity = cell.initial_cash
        self.history: dict[str, list[Bar]] = {s: [] for s in cell.symbols}
        self.append_history = append_history
        self.receivables: dict[str, float] = {}
        self.splits: dict[str, list[tuple[date, float]]] = {s: [] for s in cell.symbols}
        self.cost_basis: dict[str, float] = {}
        self.open_orders: list[dict[str, Any]] = []
        self.order_sequence = 0
        self.initial_done = not cell.initial_positions
        self.sessions_seen = 0
        self.strategies = []
        self.source_hashes = []
        for strategy_spec in cell.strategies:
            options = {"sandbox_unreviewed": True} if sandbox_unreviewed else {}
            strategy, source_hash = _load_strategy(
                strategy_spec.entrypoint, dict(strategy_spec.parameters), strategy_root, **options
            )
            snapshot = getattr(strategy, "snapshot", None)
            restore = getattr(strategy, "restore", None)
            if callable(snapshot) != callable(restore):
                raise ValueError(f"{strategy_spec.entrypoint} must implement both snapshot and restore")
            self.strategies.append((strategy_spec, strategy))
            self.source_hashes.append(source_hash)
        self.ablation = frozenset(cell.ablation)
        self.ask_gateway = None
        if cell.ask is not None:
            from .ask import AskGateway

            if cell.ask["approved_script_sha256"] not in self.source_hashes and \
                    cell.ask["approved_script_sha256"] != _allocator_digest(cell, strategy_root):
                raise ValueError(f"{cell.name}: approved ask script changed after review")
            # An ablated model is never called, so it needs no connection.
            if "ask" not in self.ablation:
                self.ask_gateway = AskGateway(dict(cell.ask), Path(cell.ask["workspace"]))
                if cell.ask["mode"] == "record":
                    self.ask_gateway._connection()
        # Each sleeve's last target, which an ablated sleeve keeps instead of asking.
        self.sleeve_targets: dict[int, dict[str, float]] = {}
        # An allocator is always reviewed code: an unreviewed agent script is refused, never sandboxed.
        self.allocator = None
        self.allocator_hash = None
        if cell.allocator is not None:
            self.allocator, self.allocator_hash = _load_strategy(
                cell.allocator.entrypoint, dict(cell.allocator.parameters), strategy_root, method="allocate")
            snapshot = getattr(self.allocator, "snapshot", None)
            restore = getattr(self.allocator, "restore", None)
            if callable(snapshot) != callable(restore):
                raise ValueError(f"{cell.allocator.entrypoint} must implement both snapshot and restore")
        self.last_allocation: list[float] | None = None
        if cell.initial_positions and len(self.strategies) != 1:
            raise ValueError(f"{cell.name}: starting holdings need a cell with one strategy")
        if saved is not None:
            self.restore_checkpoint(saved, prior_history or {})

    def restore_checkpoint(self, saved: dict[str, Any], prior_history: dict[str, list[Bar]]) -> None:
        if set(saved["positions"]) != set(self.cell.symbols) or set(prior_history) != set(self.cell.symbols):
            raise ValueError(f"Invalid saved symbols for {self.cell.name}")
        if len(saved["strategy_states"]) != len(self.strategies):
            raise ValueError(f"Invalid saved strategy count for {self.cell.name}")
        self.cash = float(saved["cash"])
        self.positions = dict(saved["positions"])
        self.pending = saved["pending"]
        self.last_date = None if saved["last_date"] is None else date.fromisoformat(saved["last_date"])
        self.equity = float(saved["equity"])
        self.receivables = dict(saved.get("receivables", {}))
        # Checkpoints written before split-adjusted history have no "splits";
        # their earlier splits stay unadjusted in the strategy view.
        self.splits = {s: [] for s in self.cell.symbols}
        for symbol, entries in saved.get("splits", {}).items():
            self.splits[symbol] = [(date.fromisoformat(day), ratio) for day, ratio in entries]
        self.history = prior_history
        self.cost_basis = {s: float(v) for s, v in saved.get("cost_basis", {}).items()}
        self.open_orders = [dict(order) for order in saved.get("open_orders", [])]
        self.order_sequence = int(saved.get("order_sequence", 0))
        self.initial_done = bool(saved.get("initial_done", True))
        for index, (_, strategy) in enumerate(self.strategies):
            restore = getattr(strategy, "restore", None)
            if callable(restore):
                restore(saved["strategy_states"][index])
        restore = getattr(self.allocator, "restore", None)
        if callable(restore) and "allocator_state" in saved:
            restore(saved["allocator_state"])

    def strategy_events(self, journal: Any, day: date) -> None:
        for (strategy_spec, _), source_hash in zip(self.strategies, self.source_hashes):
            details = {
                "entrypoint": strategy_spec.entrypoint,
                "allocation": strategy_spec.allocation,
                "parameters": strategy_spec.parameters,
                "source_sha256": source_hash,
            }
            if self.information_end is not None:
                details["information_end"] = self.information_end.isoformat()
                details["information_end_source"] = ("model_card_bound" if self.model_card
                                                     else "cell_config_unverified")
            if self.model_card is not None:
                details["model_id"] = self.model_card["model_id"]
                details["model_card_sha256"] = self.model_card["sha256"]
            journal.event(self.cell.name, day.isoformat(), "strategy", details)

    def checkpoint(self) -> dict[str, Any]:
        states = []
        for _, strategy in self.strategies:
            snapshot = getattr(strategy, "snapshot", None)
            states.append(snapshot() if callable(snapshot) else None)
        result = {
            "cash": self.cash,
            "positions": self.positions.copy(),
            "pending": None if self.pending is None else self.pending.copy(),
            "last_date": self.last_date.isoformat() if self.last_date else None,
            "equity": self.equity,
            "receivables": self.receivables.copy(),
            "strategy_states": states,
        }
        if any(self.splits.values()):
            result["splits"] = {symbol: [[day.isoformat(), ratio] for day, ratio in entries]
                                for symbol, entries in self.splits.items() if entries}
        # Written only when used, so existing checkpoints keep their shape.
        snapshot = getattr(self.allocator, "snapshot", None)
        if callable(snapshot):
            result["allocator_state"] = snapshot()
        if self.cost_basis:
            result["cost_basis"] = dict(self.cost_basis)
        if self.open_orders:
            result["open_orders"] = [dict(order) for order in self.open_orders]
        if self.order_sequence:
            result["order_sequence"] = self.order_sequence
        if self.cell.initial_positions:
            result["initial_done"] = self.initial_done
        return result

    def _buy_initial_positions(self, day: date, bars: dict[str, Bar], journal: Any) -> None:
        """Buy the configured starting holdings at the first session's open."""
        for symbol in sorted(self.cell.initial_positions):
            shares, cost = self.cell.initial_positions[symbol]
            if symbol not in bars:
                raise ValueError(f"{self.cell.name}: the starting {symbol} holding does not trade on {day}")
            price = bars[symbol].open * (1 + _slip(self.spec.costs, self.spec.slippage_bps, None))
            commission = _commission(self.spec.costs, self.spec.commission_per_order, shares)
            if self.cash + 1e-9 < shares * price + commission:
                raise ValueError(f"{self.cell.name}: initial_cash cannot buy the starting {symbol} holding")
            self.cash -= shares * price + commission
            self.positions[symbol] += shares
            self.cost_basis[symbol] = price if cost is None else float(cost)
            journal.event(self.cell.name, day.isoformat(), "order", {
                "side": "buy", "symbol": symbol, "requested_shares": shares, "filled_shares": shares,
                "fill_price": price, "commission": commission, "reason": "starting_holding",
                "cost_basis": self.cost_basis[symbol],
            })
        self.cash = max(0.0, self.cash)
        self.initial_done = True

    def _close_delisted(self, day: date, bars: dict[str, Bar], journal: Any, universe: Universe) -> None:
        """Pay out holdings in symbols that traded before but have no bar today, and cancel their orders.

        A delisting pays its stated price per share, or the last close when it
        gives none; it is not a trade, so it pays no slippage or commission.
        """
        for symbol in self.cell.symbols:
            if symbol in bars or not self.history[symbol]:
                continue
            shares = self.positions[symbol]
            orders = [order for order in self.open_orders if order["symbol"] == symbol]
            if not shares and not orders:
                continue
            delisting = universe.delistings.get(symbol)
            last_close = self.history[symbol][-1].close
            price = last_close if delisting is None or delisting.price is None else delisting.price
            if shares:
                self.cash += shares * price
                self.positions[symbol] = 0
                self.cost_basis.pop(symbol, None)
                journal.event(self.cell.name, day.isoformat(), "delisted", {
                    "symbol": symbol, "shares": shares, "price": price, "proceeds": shares * price,
                    "last_close": last_close, "reason": delisting.reason if delisting else "prices_stopped",
                    "delisting_date": delisting.date.isoformat() if delisting else None,
                })
            for order in orders:
                journal.event(self.cell.name, day.isoformat(), "order_cancelled", order | {"reason": "delisted"})
            self.open_orders = [order for order in self.open_orders if order["symbol"] != symbol]

    def _fill_open_orders(self, day: date, bars: dict[str, Bar], journal: Any,
                          previous_volumes: dict[str, int],
                          already_filled: dict[str, int] | None = None) -> None:
        """Check resting orders against today's bar: stops first, then limits.

        A daily bar cannot show which price came first, so on a day that
        touches both a stop and a limit the stop is assumed to fill first.
        """
        costs, commission = self.spec.costs, self.spec.commission_per_order
        previous = {symbol: self.history[symbol][-1] for symbol in self.cell.symbols if self.history[symbol]}
        caps = ({symbol: max(0, math.floor(previous_volumes.get(symbol, 0) *
                                         self.cell.max_previous_volume_participation)
                             - (already_filled or {}).get(symbol, 0))
                 for symbol in self.cell.symbols}
                if self.cell.max_previous_volume_participation is not None else {})
        remaining = []
        rank = {"market": 0, "stop": 1, "limit": 2}
        ordered = sorted(self.open_orders, key=lambda o: (rank[o["kind"]], o["symbol"], o["id"]))
        for order in ordered:
            symbol, side = order["symbol"], order["side"]
            trigger = _trigger_price(order, bars[symbol])
            if trigger is None:
                order = dict(order)
                if order["sessions_left"] is not None:
                    order["sessions_left"] -= 1
                    if order["sessions_left"] <= 0:
                        journal.event(self.cell.name, day.isoformat(), "order_expired", order)
                        continue
                remaining.append(order)
                continue
            # Stops and market orders pay slippage; a limit fills at its price or better.
            slip = _slip(costs, self.spec.slippage_bps, previous.get(symbol))
            price = trigger if order["kind"] == "limit" else trigger * (1 - slip if side == "sell" else 1 + slip)
            if side == "sell":
                filled = min(order["shares"], self.positions[symbol], caps.get(symbol, order["shares"]))
                if filled and self.cash + filled * price < _commission(costs, commission, filled):
                    filled = 0
            else:
                filled = min(order["shares"], _affordable(self.cash, price, commission, costs),
                             caps.get(symbol, order["shares"]))
            fee = _commission(costs, commission, filled)
            event = {"side": side, "symbol": symbol, "requested_shares": order["shares"],
                     "filled_shares": filled, "fill_price": price if filled else None,
                     "commission": fee if filled else 0, "order_type": order["kind"],
                     "order_price": order["price"], "order_id": order["id"], "tag": order["tag"]}
            if filled:
                if symbol in caps:
                    caps[symbol] -= filled
                before = dict(self.positions)
                if side == "sell":
                    self.cash += filled * price - fee
                    self.positions[symbol] -= filled
                else:
                    self.cash -= filled * price + fee
                    self.positions[symbol] += filled
                _update_cost_basis(self.cost_basis, self.positions, before, [(symbol, side, filled, price)])
            else:
                if side == "sell" and self.positions[symbol] == 0:
                    event["unfilled_reason"] = "no_shares_held"
                elif caps.get(symbol, 1) == 0:
                    event["unfilled_reason"] = "previous_volume_cap"
                else:
                    event["unfilled_reason"] = "insufficient_cash"
            journal.event(self.cell.name, day.isoformat(), "order", event)
            unfilled_shares = order["shares"] - filled
            if unfilled_shares:
                order = dict(order)
                order["shares"] = unfilled_shares
                if order["kind"] == "market":
                    journal.event(self.cell.name, day.isoformat(), "order_cancelled",
                                  order | {"reason": "market_session_complete"})
                    continue
                if order["sessions_left"] is not None:
                    order["sessions_left"] -= 1
                    if order["sessions_left"] <= 0:
                        journal.event(self.cell.name, day.isoformat(), "order_expired", order)
                        continue
                remaining.append(order)
        if self.cash < -1e-7:
            raise AssertionError("Order fills produced negative cash")
        self.cash = max(0.0, self.cash)
        # Like a broker's bracket: once nothing is held, its other sell orders go.
        kept = []
        for order in sorted(remaining, key=lambda o: o["id"]):
            if order["side"] == "sell" and self.positions[order["symbol"]] == 0:
                journal.event(self.cell.name, day.isoformat(), "order_cancelled",
                              order | {"reason": "no_shares_held"})
            else:
                kept.append(order)
        self.open_orders = kept

    def _set_orders(self, day: date, orders: list[Order], journal: Any) -> None:
        if self.open_orders:
            journal.event(self.cell.name, day.isoformat(), "orders_replaced",
                          {"cancelled": [order["id"] for order in self.open_orders]})
        self.open_orders = []
        for order in orders:
            self.order_sequence += 1
            entry = {"id": self.order_sequence, "symbol": order.symbol, "side": order.side,
                     "kind": order.kind, "shares": order.shares, "price": float(order.price),
                     "sessions_left": order.expires_after_sessions, "tag": order.tag,
                     "placed": day.isoformat()}
            self.open_orders.append(entry)
            journal.event(self.cell.name, day.isoformat(), "order_placed", entry)

    def _allocate(self, context: Context, proposals: list[dict[str, float]], day: date,
                  session_number: int, journal: Any) -> list[float]:
        """Each sleeve's share of the money: the allocator's, or the fixed allocations."""
        fixed = [strategy_spec.allocation for strategy_spec, _ in self.strategies]
        if self.allocator is None or "allocator" in self.ablation:
            return fixed
        active_context = context
        asked: list[bool] = []
        if self.cell.ask is not None and self.allocator_hash == self.cell.ask["approved_script_sha256"]:
            if "ask" in self.ablation:
                def ablated(prompt: str, schema: dict, asked: list[bool] = asked) -> dict:
                    asked.append(True)
                    raise AskAblated("The language model is switched off for this Lab variant")

                active_context = replace(context, ask=ablated)
            elif self.ask_gateway is not None:
                active_context = replace(context, ask=lambda prompt, schema: self.ask_gateway.ask(
                    prompt, schema, day=day.isoformat(), session_number=session_number,
                    journal=journal, cell=self.cell.name))
        sleeves = tuple(Sleeve(index, strategy_spec.entrypoint, strategy_spec.allocation,
                               MappingProxyType(dict(weights)))
                        for index, ((strategy_spec, _), weights) in enumerate(zip(self.strategies, proposals)))
        try:
            raw = self.allocator.allocate(active_context, sleeves)
        except Exception:
            if not asked:
                raise
            raw = None
        if asked:
            # Without the model's say, the allocator keeps its last split.
            return list(self.last_allocation or fixed)
        if not isinstance(raw, (list, tuple)) or len(raw) != len(self.strategies):
            raise ValueError(f"The allocator of {self.cell.name} must return one fraction per sleeve")
        fractions = []
        for value in raw:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
                    or not 0 <= value <= 1:
                raise ValueError(f"The allocator of {self.cell.name} returned a fraction outside 0 to 1")
            fractions.append(float(value))
        if sum(fractions) > 1 + 1e-9:
            raise ValueError(f"The allocator of {self.cell.name} gave out more than 100%")
        self.last_allocation = fractions
        return fractions

    def process_day(
        self, day: date, bars: dict[str, Bar], journal: Any,
        next_session_available: bool | None,
        actions: tuple[CorporateAction, ...] = (),
        universe: Universe | None = None,
    ) -> None:
        """One session. Without ``universe`` every symbol trades every session; with
        it, ``bars`` holds only the symbols trading today (ADR-0039)."""
        if self.last_date is not None and day <= self.last_date:
            raise ValueError(f"{self.cell.name} already processed {day}")
        if universe is None and set(bars) != set(self.cell.symbols):
            raise ValueError(f"{self.cell.name} needs all configured symbols on {day}")
        if universe is not None and not set(bars) <= set(self.cell.symbols):
            raise ValueError(f"{self.cell.name} got bars for symbols outside the cell on {day}")
        payments: list[CorporateAction] = []
        for action in actions:
            if day not in {action.date, action.ex_date} or action.symbol not in self.cell.symbols:
                raise ValueError("Corporate action does not belong to this cell or day")
            if action.kind == "split":
                if action.date != day:
                    raise ValueError("Split date does not match the current session")
                old_shares = self.positions[action.symbol]
                new_shares = old_shares * action.value
                if not math.isclose(new_shares, round(new_shares), abs_tol=1e-9):
                    raise ValueError(
                        f"Fractional shares from split of {action.symbol} on {day} need cash-in-lieu modeling"
                    )
                adjusted_orders = []
                for order in self.open_orders:
                    if order["symbol"] != action.symbol:
                        continue
                    adjusted_shares = order["shares"] * action.value
                    if not math.isclose(adjusted_shares, round(adjusted_shares), abs_tol=1e-9):
                        raise ValueError(
                            f"Fractional open order from split of {action.symbol} on {day} "
                            "needs cash-in-lieu modeling"
                        )
                    adjusted_orders.append((order, round(adjusted_shares)))
                self.positions[action.symbol] = round(new_shares)
                if old_shares and action.symbol in self.cost_basis:
                    self.cost_basis[action.symbol] /= action.value
                for order, shares in adjusted_orders:
                    original = {"shares": order["shares"], "price": order["price"]}
                    order["shares"] = shares
                    order["price"] /= action.value
                    journal.event(self.cell.name, day.isoformat(), "order_adjusted", {
                        "order_id": order["id"], "symbol": action.symbol, "split_ratio": action.value,
                        "before": original, "after": {"shares": shares, "price": order["price"]},
                    })
                self.splits[action.symbol].append((day, action.value))
                journal.event(self.cell.name, day.isoformat(), "corporate_action", {
                    "kind": "split", "symbol": action.symbol, "ratio": action.value,
                    "source_id": action.source_id,
                    "shares_before": old_shares, "shares_after": self.positions[action.symbol],
                })
            elif action.kind == "dividend":
                key = action.source_id or f"{action.symbol}:{action.date.isoformat()}"
                if day == action.ex_date:
                    # Orders for this session have not filled yet, so these are
                    # the shares held at the previous close.
                    eligible = self.positions[action.symbol]
                    self.receivables[key] = eligible * action.value
                    journal.event(self.cell.name, day.isoformat(), "corporate_action", {
                        "kind": "dividend_entitlement", "symbol": action.symbol,
                        "source_id": action.source_id,
                        "payment_date": action.date.isoformat(), "per_share": action.value,
                        "eligible_shares": eligible, "receivable": self.receivables[key],
                    })
                if day == action.date:
                    payments.append(action)
            else:
                raise ValueError(f"Unsupported corporate action: {action.kind}")
        if universe is not None:
            self._close_delisted(day, bars, journal, universe)
        if not self.initial_done:
            self._buy_initial_positions(day, bars, journal)
        previous_volumes = (
            # Prior volume in today's share units, so a split does not
            # shrink or inflate the participation cap. In a changing universe a
            # symbol without a previous session gets no volume, and so no fill.
            {symbol: BarHistory(self.history[symbol], None, self.splits[symbol])[-1].volume
             for symbol in self.cell.symbols if self.history[symbol]}
            if self.cell.max_previous_volume_participation is not None
            and (universe is not None or all(self.history[symbol] for symbol in self.cell.symbols)) else {})
        fills: list = []
        if self.pending is not None:
            before = dict(self.positions)
            self.cash = _rebalance(
                self.cell, day, bars, self.pending, self.cash, self.positions,
                self.spec.slippage_bps, self.spec.commission_per_order, journal,
                previous_volumes, fills=fills, costs=self.spec.costs,
                previous_bars={symbol: self.history[symbol][-1] for symbol in self.cell.symbols
                               if self.history[symbol]},
            )
            _update_cost_basis(self.cost_basis, self.positions, before, fills)
        if self.open_orders:
            used_volume = defaultdict(int)
            for symbol, _, shares, _ in fills:
                used_volume[symbol] += shares
            self._fill_open_orders(day, bars, journal, previous_volumes, used_volume)
        for action in payments:
            key = action.source_id or f"{action.symbol}:{action.date.isoformat()}"
            cash_paid = self.receivables.pop(key, 0)
            self.cash += cash_paid
            journal.event(self.cell.name, day.isoformat(), "corporate_action", {
                "kind": "dividend_payment", "symbol": action.symbol,
                "source_id": action.source_id,
                "ex_date": action.ex_date.isoformat(), "cash_paid": cash_paid,
            })
        priced = [s for s in self.cell.symbols if s in bars]
        if universe is not None and any(self.positions[s] for s in self.cell.symbols if s not in bars):
            raise AssertionError(f"{self.cell.name} holds a symbol with no price on {day}")
        self.equity = (self.cash + sum(self.positions[s] * bars[s].close for s in priced)
                       + sum(self.receivables.values()))
        valuation = {
            "cash": self.cash, "positions": self.positions.copy(), "equity": self.equity,
            "receivables": self.receivables.copy(),
            "prices": {s: bars[s].close for s in priced},
        }
        if self.information_end is not None:
            valuation["information_end"] = self.information_end.isoformat()
            valuation["post_information_end"] = day > self.information_end
            valuation["information_end_source"] = ("model_card_bound" if self.model_card
                                                   else "cell_config_unverified")
        journal.event(self.cell.name, day.isoformat(), "valuation", valuation)
        # What the strategy sees: every symbol, or in a changing universe the
        # symbols it may hold after this close (trading today, and members of its index).
        if universe is None:
            visible = list(self.cell.symbols)
        else:
            visible = [s for s in self.cell.symbols if s in bars and
                       (self.cell.index is None or universe.member(self.cell.index, s, day))]
        history = MappingProxyType({s: BarHistory(self.history[s], bars[s], self.splits[s])
                                    for s in visible})
        estimates = {}
        if self.model_card is not None and "risk" not in self.ablation:
            from .ml.card import predict, risk_features

            estimates = {symbol: predict(self.model_card, features)
                         for symbol in history
                         if (features := risk_features(history[symbol])) is not None}
            if estimates:
                journal.event(self.cell.name, day.isoformat(), "model_estimate", {
                    "model_id": self.model_card["model_id"],
                    "model_card_sha256": self.model_card["sha256"],
                    "risk_of_drop": estimates,
                    "information_end": self.information_end.isoformat(),
                    "post_information_end": day > self.information_end,
                    "source_status": self.model_card["data_manifest"]["source_status"],
                })
        context = Context(
            date=day,
            bars=MappingProxyType(bars.copy() if universe is None else {s: bars[s] for s in visible}),
            history=history,
            cash=self.cash,
            positions=MappingProxyType(self.positions.copy()),
            equity=self.equity,
            cost_basis=MappingProxyType(dict(self.cost_basis)),
            open_orders=tuple(Order(o["symbol"], o["side"], o["kind"], o["shares"], o["price"],
                                    o["sessions_left"], o["tag"]) for o in self.open_orders),
            risk_estimates=MappingProxyType(estimates),
        )
        if universe is None:
            session_number = len(self.history[self.cell.symbols[0]]) + 1
        else:
            self.sessions_seen += 1
            session_number = self.sessions_seen
        if (session_number - 1) % self.cell.rebalance_every_sessions:
            self.pending = None
            journal.event(self.cell.name, day.isoformat(), "rebalance_skipped", {
                "session_number": session_number,
                "rebalance_every_sessions": self.cell.rebalance_every_sessions,
            })
            self.last_date = day
            if self.append_history:
                for symbol, bar in bars.items():
                    self.history[symbol].append(bar)
            return
        if universe is not None and not visible:
            # Nothing the cell may hold trades yet, or any more: it stays in cash.
            self.pending = {}
            journal.event(self.cell.name, day.isoformat(), "signal", {
                "decisions": [], "target_weights": {}, "execute_on_next_session": next_session_available,
                "empty_universe": True})
            self.last_date = day
            for symbol, bar in bars.items():
                self.history[symbol].append(bar)
            return
        combined: dict[str, float] = defaultdict(float)
        decisions = []
        hold = False
        new_orders: list[Order] | None = None
        proposals: list[dict[str, float]] = []
        for index, (strategy_spec, strategy) in enumerate(self.strategies):
            active_context = context
            asked: list[bool] = []
            granted = self.cell.ask is not None and \
                self.source_hashes[index] == self.cell.ask["approved_script_sha256"]
            if granted and "ask" in self.ablation:
                def ablated(prompt: str, schema: dict, asked: list[bool] = asked) -> dict:
                    asked.append(True)
                    raise AskAblated("The language model is switched off for this Lab variant")

                active_context = replace(context, ask=ablated)
            elif granted and self.ask_gateway is not None:
                active_context = replace(context, ask=lambda prompt, schema: self.ask_gateway.ask(
                    prompt, schema, day=day.isoformat(), session_number=session_number,
                    journal=journal, cell=self.cell.name))
            try:
                raw = strategy.on_bar(active_context)
            except Exception:
                if not asked:
                    raise
                raw = None
            if asked:
                # Without the model's say, this sleeve keeps its last target.
                kept = dict(self.sleeve_targets.get(index, {}))
                proposals.append(kept)
                decisions.append({"entrypoint": strategy_spec.entrypoint, "weights": kept,
                                  "notes": {"ablated": "ask"}})
                continue
            notes = None
            if isinstance(raw, Decision):
                if len(self.strategies) != 1 or self.allocator is not None:
                    raise ValueError(f"A Decision needs a cell with one strategy and no allocator ({self.cell.name})")
                if raw.orders is not None:
                    new_orders = _validate_orders(raw.orders, self.cell)
                notes = None if raw.notes is None else dict(raw.notes)
                if raw.weights is None:
                    hold = True
                    decisions.append({"entrypoint": strategy_spec.entrypoint, "weights": None, "notes": notes})
                    continue
                raw = raw.weights
            weights = _validate_weights(raw, self.cell)
            self.sleeve_targets[index] = weights
            proposals.append(weights)
            entry = {"entrypoint": strategy_spec.entrypoint, "weights": weights}
            if notes is not None:
                entry["notes"] = notes
            decisions.append(entry)
        allocation = self._allocate(context, proposals, day, session_number, journal) \
            if proposals else None
        for fraction, weights in zip(allocation or [], proposals):
            if not fraction:
                continue  # a sleeve given nothing adds nothing to the targets
            for symbol, weight in weights.items():
                combined[symbol] += fraction * weight
        # Weight on a symbol the cell may not hold stays in cash, as an order a broker would refuse.
        outside = sorted(s for s, w in combined.items() if w > 0 and s not in visible) if universe is not None else []
        for symbol in outside:
            del combined[symbol]
        for symbol, weight in combined.items():
            if weight > self.cell.max_position_weight + 1e-12:
                raise ValueError(
                    f"Target weight for {symbol} in {self.cell.name} exceeds max_position_weight"
                )
        if sum(combined.values()) > 1 - self.cell.min_cash_weight + 1e-12:
            raise ValueError(f"Target weights in {self.cell.name} violate min_cash_weight")
        # A Decision without weights keeps today's holdings: no rebalance tomorrow.
        self.pending = None if hold else dict(combined)
        signal = {"decisions": decisions, "target_weights": self.pending,
                  "execute_on_next_session": next_session_available}
        if self.allocator is not None:
            signal["allocation"] = allocation
        if outside:
            signal["outside_universe"] = outside
        journal.event(self.cell.name, day.isoformat(), "signal", signal)
        if new_orders is not None and universe is not None:
            kept = []
            for order in new_orders:
                if order.symbol in visible or (order.side == "sell" and order.symbol in bars):
                    kept.append(order)
                else:
                    journal.event(self.cell.name, day.isoformat(), "order_rejected", {
                        "symbol": order.symbol, "side": order.side, "kind": order.kind, "shares": order.shares,
                        "price": order.price, "tag": order.tag, "reason": "outside_universe"})
            new_orders = kept
        if new_orders is not None:
            self._set_orders(day, new_orders, journal)
        self.last_date = day
        if self.append_history:
            for symbol, bar in bars.items():
                self.history[symbol].append(bar)


def _allocator_digest(cell: CellSpec, strategy_root: Path) -> str | None:
    """The SHA-256 of a cell's allocator script, when it is a workspace file."""
    if cell.allocator is None:
        return None
    module = cell.allocator.entrypoint.split(":", 1)[0]
    path = strategy_root.resolve() / f"{module}.py"
    return hashlib.sha256(path.read_bytes()).hexdigest() if module.isidentifier() and path.is_file() else None


def run_static(spec: RunSpec, config_json: str, strategy_root: Path,
               metadata: dict | None = None) -> str:
    symbols = {symbol for cell in spec.cells for symbol in cell.symbols}
    if spec.changing_universe:
        days, tape, data_hash = _universe_tape(Path(spec.data_path), symbols, spec.symbol_map)
    else:
        days, tape, data_hash = load_daily_csv(Path(spec.data_path), symbols, spec.symbol_map)
    universe = _universe(spec, days, tape, symbols) if spec.changing_universe else None
    if universe is not None:
        metadata = {**(metadata or {}), "changing_universe": {
            name: hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            for name, path in (("delistings", spec.delistings_path), ("membership", spec.membership_path)) if path}}
    if spec.symbol_map:
        mapping = json.dumps(dict(spec.symbol_map), sort_keys=True)
        data_hash = hashlib.sha256(f"{data_hash}:map:{mapping}".encode()).hexdigest()
    actions: dict[date, list[CorporateAction]] = {}
    if spec.actions_path:
        actions, action_hash = load_actions_csv(Path(spec.actions_path), symbols, set(days),
                                                spec.symbol_map)
        data_hash = hashlib.sha256(f"{data_hash}:{action_hash}".encode()).hexdigest()
        for payable_day, payable_actions in list(actions.items()):
            for action in payable_actions:
                if action.kind == "dividend" and action.ex_date != payable_day:
                    actions.setdefault(action.ex_date, []).append(action)
        for day, day_actions in actions.items():
            if day not in tape:
                if day <= days[-1] or any(action.kind != "dividend" for action in day_actions):
                    raise ValueError(f"Corporate-action date {day} has no daily bar")
                continue
            by_symbol: dict[str, list[CorporateAction]] = defaultdict(list)
            for action in day_actions:
                by_symbol[action.symbol].append(action)
            for symbol, same_day in by_symbol.items():
                splits = [action for action in same_day if action.kind == "split"]
                ex_dividends = [action for action in same_day
                                if action.kind == "dividend" and action.ex_date == day]
                if len(splits) > 1 or (splits and ex_dividends):
                    raise ValueError(f"Mixed or multiple splits for {symbol} on {day} are not modeled")
    journal = Journal(Path(spec.database_path), config_json, data_hash, metadata)
    try:
        for cell in spec.cells:
            journal.add_cell(cell)
            # Static simulations may run unreviewed agent scripts, in the sandbox.
            runtime = CellRuntime(cell, spec, strategy_root, sandbox_unreviewed=True)
            runtime.strategy_events(journal, days[0])
            journal.checkpoint()
            committed_equity = runtime.equity
            day = days[0]
            try:
                for index, day in enumerate(days):
                    bars = {symbol: tape[day][symbol] for symbol in cell.symbols
                            if universe is None or symbol in tape[day]}
                    cell_actions = tuple(action for action in actions.get(day, ())
                                         if action.symbol in cell.symbols)
                    runtime.process_day(day, bars, journal, index < len(days) - 1, cell_actions,
                                        universe=universe)
                    journal.checkpoint()
                    committed_equity = runtime.equity
            except Exception as exc:
                if isinstance(exc, sqlite3.Error):
                    raise
                # The failing day's partial events are discarded; the cell
                # ends at its last completed day and the other cells still run.
                journal.rollback()
                journal.fail_cell(cell.name, day.isoformat(),
                                  f"{type(exc).__name__}: {exc}", committed_equity)
                continue
            journal.finish_cell(cell.name, runtime.equity)
        journal.finish("completed")
        return journal.run_id
    except BaseException as exc:
        journal.finish("failed", f"{type(exc).__name__}: {exc}")
        raise

