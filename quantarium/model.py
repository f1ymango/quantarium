"""Shared data types for the static replay engine."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from types import MappingProxyType
from datetime import date, datetime
from collections import deque
from typing import Callable, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class Bar:
    date: date
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True, slots=True)
class MinuteBar:
    timestamp: datetime
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class CorporateAction:
    date: date
    symbol: str
    kind: str
    value: float
    ex_date: date
    source_id: str | None = None
    process_date: date | None = None


class BarHistory(Sequence[Bar]):
    """Read-only view of a symbol's bars through a fixed point in time.

    ``splits`` lists ``(effective date, ratio)`` for splits already applied to
    the account, in date order. Bars dated before a split are shown divided by
    its ratio (volume multiplied), so a 2-for-1 split does not look like a 50%
    price drop. The stored bars and the account stay on raw prices.
    """

    def __init__(self, values: Sequence[Bar] | Sequence[MinuteBar],
                 current: Bar | MinuteBar | None = None,
                 splits: Sequence[tuple[date, float]] = ()):
        self._values = tuple(values) if isinstance(values, deque) else values
        self._end = len(values)
        self._current = current
        self._split_dates = [effective for effective, _ in splits]
        # _factors[k] is the combined ratio of splits[k:], the adjustment for a
        # bar dated before splits[k] and on or after splits[k - 1].
        self._factors = [1.0] * (len(splits) + 1)
        for index in range(len(splits) - 1, -1, -1):
            self._factors[index] = self._factors[index + 1] * splits[index][1]

    def _adjusted(self, bar: Bar | MinuteBar) -> Bar | MinuteBar:
        if not self._split_dates:
            return bar
        factor = self._factors[bisect_right(self._split_dates, bar.date)]
        if factor == 1:
            return bar
        return Bar(bar.date, bar.symbol, bar.open / factor, bar.high / factor,
                   bar.low / factor, bar.close / factor, round(bar.volume * factor))

    def __len__(self) -> int:
        return self._end + (self._current is not None)

    def __getitem__(self, index: int | slice) -> Bar | MinuteBar | tuple[Bar | MinuteBar, ...]:
        if isinstance(index, slice):
            return tuple(self[i] for i in range(*index.indices(len(self))))
        if index < 0:
            index += len(self)
        if index < 0 or index >= len(self):
            raise IndexError("bar history index out of range")
        if index == self._end:
            assert self._current is not None
            return self._adjusted(self._current)
        return self._adjusted(self._values[index])


def _ask_disabled(prompt: str, schema: dict) -> dict:
    raise ValueError("context.ask is not enabled for this script")


@dataclass(frozen=True)
class Context:
    """Information available to a strategy after the current daily close."""

    date: date
    bars: Mapping[str, Bar]
    history: Mapping[str, Sequence[Bar]]
    cash: float
    positions: Mapping[str, int]
    equity: float
    # Average price paid per share held, after slippage, excluding commission.
    # Starting holdings use the cost the user supplied.
    cost_basis: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({}))
    # Limit and stop orders still open after today's session.
    open_orders: tuple["Order", ...] = ()
    # Model estimates available at this close; values are predictions, not facts.
    risk_estimates: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({}))
    # The engine grants this only to an explicitly reviewed script version.
    ask: Callable[[str, dict], dict] = _ask_disabled


ORDER_SIDES = ("buy", "sell")
ORDER_KINDS = ("limit", "stop", "market")


@dataclass(frozen=True)
class Order:
    """A resting order, placed after a close and active from the next session.

    A sell stop fills when the low reaches ``price``, a sell limit when the
    high does; buys mirror this. An order a gap jumps over fills at the open.
    A market order fills at the next open; its ``price`` is only a reference.
    ``expires_after_sessions`` counts sessions the order may fill in; None
    keeps it until filled or replaced.
    """

    symbol: str
    side: str
    kind: str
    shares: int
    price: float
    expires_after_sessions: int | None = None
    tag: str = ""


@dataclass(frozen=True)
class Decision:
    """What a strategy may return instead of plain weights.

    ``weights`` is a target as before, or None to leave holdings alone.
    ``orders`` replaces every open order of the cell (an empty tuple cancels
    them all), or None keeps them. ``notes`` are saved with the signal.
    """

    weights: object = None
    orders: tuple[Order, ...] | None = None
    notes: Mapping[str, object] | None = None


@dataclass(frozen=True)
class LiveContext:
    """Information available after a completed minute bar is finalized."""

    timestamp: datetime
    date: date
    bars: Mapping[str, MinuteBar]
    history: Mapping[str, Sequence[MinuteBar]]
    cash: float
    positions: Mapping[str, int]
    equity: float
    quiet_symbols: frozenset[str] = frozenset()


@dataclass(frozen=True)
class StrategySpec:
    entrypoint: str
    allocation: float
    parameters: Mapping[str, object]


@dataclass(frozen=True)
class Sleeve:
    """One sleeve's proposal, as an allocator sees it."""

    index: int
    entrypoint: str
    allocation: float
    weights: Mapping[str, float]


@dataclass(frozen=True)
class CellSpec:
    name: str
    mode: str
    symbols: tuple[str, ...]
    initial_cash: float
    strategies: tuple[StrategySpec, ...]
    rebalance_every_sessions: int = 1
    max_position_weight: float = 1.0
    min_cash_weight: float = 0.0
    max_previous_volume_participation: float | None = None
    # Holdings bought at the first session's open, as {symbol: (shares, cost)}.
    # ``cost`` is what the user originally paid per share, or None to use the fill.
    initial_positions: Mapping[str, tuple[int, float | None]] = field(
        default_factory=lambda: MappingProxyType({}))
    # A declared upper bound on information used to prepare a model or rule.
    # The Lab must verify its provenance before granting an evidence verdict.
    information_end: date | None = None
    # Workspace-relative immutable JSON model card, when this cell uses one.
    model_card: str | None = None
    # Explicit gateway grant; the API pins its reviewed strategy bytes.
    ask: Mapping[str, object] | None = None
    # Parts switched off for a Lab ablation: "ask" (the language model), "risk" (the model card),
    # and "allocator" (dynamic sleeve allocation, replaced by the fixed allocations).
    ablation: tuple[str, ...] = ()
    # Splits a Complex cell's money between its sleeves at each close, instead of fixed allocations.
    allocator: "StrategySpec | None" = None
    # In a changing universe: hold only this index's members as of each close.
    index: str | None = None


@dataclass(frozen=True)
class CostModel:
    """Trading costs beyond a flat slippage and a flat commission per order; all zero by default.

    A filled order's commission is the flat commission plus ``commission_per_share``
    for each share, but at least ``commission_minimum``. Slippage adds
    ``slippage_range_fraction`` times the previous session's range (high less
    low, over its close) to the flat slippage, so volatile stocks cost more to
    trade; the first session has no previous range.
    """

    commission_per_share: float = 0.0
    commission_minimum: float = 0.0
    slippage_range_fraction: float = 0.0

    def as_dict(self) -> dict:
        return {"commission_per_share": self.commission_per_share, "commission_minimum": self.commission_minimum,
                "slippage_range_fraction": self.slippage_range_fraction}


COST_FIELDS = ("commission_per_share", "commission_minimum", "slippage_range_fraction")


@dataclass(frozen=True)
class RunSpec:
    data_path: str
    database_path: str
    slippage_bps: float
    commission_per_order: float
    cells: tuple[CellSpec, ...]
    actions_path: str | None = None
    # Input symbol -> cell symbol, used to run cells on a scenario's tape.
    symbol_map: Mapping[str, str] | None = None
    costs: CostModel = field(default_factory=CostModel)
    # Symbols may list, delist, and change index membership during the run (ADR-0039).
    changing_universe: bool = False
    delistings_path: str | None = None
    membership_path: str | None = None


@dataclass(frozen=True)
class FeedSpec:
    feed_id: str
    start_date: date
    provider: str
    market_feed: str
    database_path: str
    slippage_bps: float
    commission_per_order: float
    cells: tuple[CellSpec, ...]
    ignore_corporate_actions: bool = False
    apply_supported_corporate_actions: bool = False
    # With provider "scenario", the scenario directory released session by session.
    scenario_path: str | None = None
    # With provider "csv", a Static-format price file the user keeps appending to.
    data_path: str | None = None
    costs: CostModel = field(default_factory=CostModel)

    def runtime_spec(self) -> RunSpec:
        return RunSpec("", self.database_path, self.slippage_bps, self.commission_per_order, self.cells,
                       costs=self.costs)


@dataclass(frozen=True)
class LiveSpec:
    feed_id: str
    start_at: datetime
    stop_at: datetime
    provider: str
    market_feed: str
    database_path: str
    slippage_bps: float
    commission_per_order: float
    cells: tuple[CellSpec, ...]
    history_limit: int = 390
    finalization_delay_seconds: int = 95
    input_kind: str = "websocket"
    input_sha256: str | None = None
    max_quiet_minutes: int = 0

