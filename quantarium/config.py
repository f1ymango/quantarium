"""Configuration parsing and validation."""

from __future__ import annotations

import functools
import json
import math
import re
from datetime import date, datetime, timezone
from pathlib import Path
from types import MappingProxyType

from .model import CellSpec, CostModel, FeedSpec, LiveSpec, RunSpec, StrategySpec
from .userdata import INDEX
from .provider import NEW_YORK


def plain_errors(loader):
    """Report a config with the wrong structure as a ValueError, like every other config problem."""
    @functools.wraps(loader)
    def load(path: Path):
        try:
            return loader(path)
        except (AttributeError, IndexError, OverflowError, RecursionError) as exc:
            raise ValueError(f"{Path(path).name} is malformed: {exc}") from exc
    return load


def strategy_root(path: Path, raw: str) -> Path:
    """Where a config's strategy modules load from.

    By default this is the config's own folder; ``strategy_root`` (relative
    to the config) lets a generated config use scripts kept elsewhere.
    """
    source = json.loads(raw)
    folder = source.get("strategy_root") if isinstance(source, dict) else None
    return (path.resolve().parent / folder).resolve() if folder else path.resolve().parent


def _finite_nonnegative(value: object, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{name} must be finite and nonnegative")
    return number


def cost_model(source: dict) -> CostModel:
    """The optional costs beyond flat slippage and a flat commission, from a config, suite, or request."""
    per_share = _finite_nonnegative(source.get("commission_per_share", 0), "commission_per_share")
    minimum = _finite_nonnegative(source.get("commission_minimum", 0), "commission_minimum")
    fraction = _finite_nonnegative(source.get("slippage_range_fraction", 0), "slippage_range_fraction")
    if per_share > 100 or minimum > 100_000 or fraction > 1:
        raise ValueError("commission_per_share is at most 100, commission_minimum at most 100000, "
                         "and slippage_range_fraction at most 1")
    return CostModel(per_share, minimum, fraction)


def _cells(source: dict) -> tuple[CellSpec, ...]:
    cells: list[CellSpec] = []
    names: set[str] = set()
    for item in source["cells"]:
        name = str(item["name"]).strip()
        mode = str(item["mode"]).lower()
        symbols = tuple(str(s).upper().strip() for s in item["symbols"])
        if not name or name in names:
            raise ValueError("Each cell needs a unique, nonempty name")
        names.add(name)
        if mode not in {"simple", "complex"}:
            raise ValueError(f"Invalid mode for {name}: {mode}")
        if not symbols or len(set(symbols)) != len(symbols) or any(not s for s in symbols):
            raise ValueError(f"{name} needs distinct, nonempty symbols")
        cash = _finite_nonnegative(item["initial_cash"], f"{name}.initial_cash")
        if cash <= 0:
            raise ValueError(f"{name}.initial_cash must be positive")
        for strategy in item["strategies"]:
            if not isinstance(strategy.get("parameters", {}), dict):
                raise ValueError(f"Strategy parameters in {name} must be an object")
        strategies = tuple(
            StrategySpec(
                entrypoint=str(s["entrypoint"]),
                allocation=_finite_nonnegative(s.get("allocation", 1), "allocation"),
                parameters=s.get("parameters", {}),
            )
            for s in item["strategies"]
        )
        if not strategies or sum(s.allocation for s in strategies) > 1 + 1e-12:
            raise ValueError(f"{name} needs strategies whose allocations total at most 1")
        if mode == "simple" and (len(symbols) != 1 or len(strategies) != 1 or strategies[0].allocation != 1):
            raise ValueError(f"Simple cell {name} needs one symbol and one full-allocation strategy")
        interval = item.get("rebalance_every_sessions", 1)
        if isinstance(interval, bool) or not isinstance(interval, int) or interval < 1:
            raise ValueError(f"{name}.rebalance_every_sessions must be a positive integer")
        cap = _finite_nonnegative(item.get("max_position_weight", 1),
                                  f"{name}.max_position_weight")
        reserve = _finite_nonnegative(item.get("min_cash_weight", 0),
                                      f"{name}.min_cash_weight")
        if cap > 1 or reserve > 1:
            raise ValueError(f"{name} portfolio weights must be at most 1")
        participation = item.get("max_previous_volume_participation")
        if participation is not None:
            participation = _finite_nonnegative(
                participation, f"{name}.max_previous_volume_participation"
            )
            if participation > 1:
                raise ValueError(f"{name}.max_previous_volume_participation must be at most 1")
        holdings = {}
        raw_holdings = item.get("initial_positions", {})
        if not isinstance(raw_holdings, dict):
            raise ValueError(f"{name}.initial_positions must be an object")
        for raw_symbol, entry in raw_holdings.items():
            symbol = str(raw_symbol).upper().strip()
            if symbol not in symbols:
                raise ValueError(f"{name}.initial_positions names {symbol}, which is not a cell symbol")
            if symbol in holdings:
                raise ValueError(f"{name}.initial_positions repeats {symbol} after normalization")
            if not isinstance(entry, dict):
                raise ValueError(f"{name}.initial_positions.{symbol} must be an object with shares and cost")
            shares = entry.get("shares")
            if isinstance(shares, bool) or not isinstance(shares, int) or shares <= 0:
                raise ValueError(f"{name}.initial_positions.{symbol}.shares must be a positive whole number")
            cost = entry.get("cost")
            if cost is not None:
                cost = _finite_nonnegative(cost, f"{name}.initial_positions.{symbol}.cost")
                if cost <= 0:
                    raise ValueError(f"{name}.initial_positions.{symbol}.cost must be positive")
            holdings[symbol] = (shares, cost)
        if holdings and len(strategies) != 1:
            raise ValueError(f"{name}: starting holdings need a cell with one strategy")
        raw_information_end = item.get("information_end")
        if raw_information_end is not None:
            if not isinstance(raw_information_end, str):
                raise ValueError(f"{name}.information_end must be an ISO date")
            try:
                information_end = date.fromisoformat(raw_information_end)
            except ValueError as exc:
                raise ValueError(f"{name}.information_end must be an ISO date") from exc
        else:
            information_end = None
        model_card = item.get("model_card")
        if model_card is not None and (not isinstance(model_card, str) or not model_card.strip()
                                       or ".." in Path(model_card).parts):
            raise ValueError(f"{name}.model_card must be a JSON path without traversal")
        ask = item.get("ask")
        if ask is not None:
            from .ask import validate_settings

            validate_settings(ask)
        allocator = item.get("allocator")
        if allocator is not None:
            if mode != "complex":
                raise ValueError(f"{name}.allocator needs a Complex cell")
            if not isinstance(allocator, dict) or not isinstance(allocator.get("entrypoint"), str) or \
                    ":" not in allocator["entrypoint"] or not isinstance(allocator.get("parameters", {}), dict) or \
                    set(allocator) - {"entrypoint", "parameters"}:
                raise ValueError(f"{name}.allocator needs an entrypoint (module:Class) and optional parameters")
            if holdings:
                raise ValueError(f"{name}: starting holdings need a cell with one strategy and no allocator")
            allocator = StrategySpec(entrypoint=allocator["entrypoint"], allocation=1,
                                     parameters=allocator.get("parameters", {}))
        ablation = item.get("ablation", [])
        if not isinstance(ablation, list) or not set(ablation) <= {"ask", "risk", "allocator"} or \
                len(set(ablation)) != len(ablation):
            raise ValueError(f"{name}.ablation may list ask, risk, and allocator")
        if "allocator" in ablation and allocator is None:
            raise ValueError(f"{name}.ablation names allocator, but the cell has none")
        if "ask" in ablation and ask is None:
            raise ValueError(f"{name}.ablation names ask, but the cell has no ask grant")
        if "risk" in ablation and model_card is None:
            raise ValueError(f"{name}.ablation names risk, but the cell has no model card")
        index = item.get("index")
        if index is not None and (not isinstance(index, str) or not INDEX.fullmatch(index)):
            raise ValueError(f"{name}.index must name an index in the data's membership (letters, digits, '_.-')")
        cells.append(CellSpec(name, mode, symbols, cash, strategies, interval, cap,
                              reserve, participation, MappingProxyType(holdings), information_end,
                              model_card, MappingProxyType(ask) if ask is not None else None,
                              tuple(ablation), allocator, index=index))
    if not cells:
        raise ValueError("At least one cell is required")
    return tuple(cells)


@plain_errors
def load_config(path: Path) -> tuple[RunSpec, str]:
    raw = path.read_text(encoding="utf-8")
    source = json.loads(raw)
    root = path.resolve().parent
    cells = _cells(source)
    spec = RunSpec(
        data_path=str((root / source["data_path"]).resolve()),
        database_path=str((root / source["database_path"]).resolve()),
        slippage_bps=_finite_nonnegative(source.get("slippage_bps", 0), "slippage_bps"),
        commission_per_order=_finite_nonnegative(source.get("commission_per_order", 0), "commission_per_order"),
        cells=cells,
        actions_path=(str((root / source["actions_path"]).resolve())
                      if source.get("actions_path") else None),
        costs=cost_model(source),
        **_universe_settings(source, root, cells),
    )
    if spec.slippage_bps >= 10_000:
        raise ValueError("slippage_bps must be below 10000")
    return spec, raw


def _universe_settings(source: dict, root: Path, cells: tuple[CellSpec, ...]) -> dict:
    """Changing-universe settings of a Static config: on when asked for, or when its files are given."""
    flag = source.get("changing_universe", False)
    if not isinstance(flag, bool):
        raise ValueError("changing_universe must be true or false")
    paths = {key: str((root / source[key]).resolve()) for key in ("delistings_path", "membership_path")
             if source.get(key)}
    changing = flag or bool(paths)
    if not changing and any(cell.index for cell in cells):
        raise ValueError("A cell that holds an index's members needs membership_path")
    return {"changing_universe": changing, **paths}


def _fixed_universe(cells: tuple[CellSpec, ...], where: str) -> None:
    if any(cell.index for cell in cells):
        raise ValueError(f"{where} replays a fixed set of symbols; a cell that holds an index's members "
                         f"runs in Static, a suite, or the Lab")


@plain_errors
def load_feed_config(path: Path) -> tuple[FeedSpec, str]:
    raw = path.read_text(encoding="utf-8")
    source = json.loads(raw)
    feed_id = str(source["feed_id"])
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", feed_id):
        raise ValueError("feed_id must use 1-64 letters, digits, dashes, or underscores")
    provider = str(source.get("provider", "alpaca")).lower()
    root = path.resolve().parent
    scenario_path = data_path = None
    if provider == "csv":
        if "data_path" not in source:
            raise ValueError("A csv Feed needs data_path")
        data_path = str((root / source["data_path"]).resolve())
        market_feed = "file"
    elif provider == "scenario":
        if "scenario_path" not in source:
            raise ValueError("A scenario Feed needs scenario_path")
        scenario_path = str((root / source["scenario_path"]).resolve())
        market_feed = "scenario"
    else:
        market_feed = str(source.get("market_feed", "sip")).lower()
        if provider != "alpaca" or market_feed not in {"sip", "iex"}:
            raise ValueError(
                "Feed batch supports Alpaca SIP or IEX daily bars, a csv file, or a scenario"
            )
    ignore_actions = source.get("ignore_corporate_actions", False)
    if not isinstance(ignore_actions, bool):
        raise ValueError("ignore_corporate_actions must be true or false")
    apply_actions = source.get("apply_supported_corporate_actions", False)
    if not isinstance(apply_actions, bool):
        raise ValueError("apply_supported_corporate_actions must be true or false")
    if apply_actions and ignore_actions:
        raise ValueError("Feed cannot apply and ignore corporate actions together")
    spec = FeedSpec(
        feed_id=feed_id,
        start_date=date.fromisoformat(source["start_date"]),
        provider=provider,
        market_feed=market_feed,
        database_path=str((root / source["database_path"]).resolve()),
        slippage_bps=_finite_nonnegative(source.get("slippage_bps", 0), "slippage_bps"),
        commission_per_order=_finite_nonnegative(source.get("commission_per_order", 0), "commission_per_order"),
        cells=_cells(source),
        ignore_corporate_actions=ignore_actions,
        apply_supported_corporate_actions=apply_actions,
        scenario_path=scenario_path, data_path=data_path, costs=cost_model(source),
    )
    _fixed_universe(spec.cells, "Feed Batch")
    if spec.slippage_bps >= 10_000:
        raise ValueError("slippage_bps must be below 10000")
    if any(cell.ablation for cell in spec.cells):
        raise ValueError("ablation is for Lab variants only")
    return spec, raw


@plain_errors
def load_live_config(path: Path) -> tuple[LiveSpec, str]:
    raw = path.read_text(encoding="utf-8")
    source = json.loads(raw)
    feed_id = str(source["feed_id"])
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", feed_id):
        raise ValueError("feed_id must use 1-64 letters, digits, dashes, or underscores")
    provider = str(source.get("provider", "alpaca")).lower()
    market_feed = str(source.get("market_feed", "iex")).lower()
    if provider != "alpaca" or market_feed not in {"sip", "iex"}:
        raise ValueError("Live Feed currently supports Alpaca SIP or IEX minute bars")

    def timestamp(key: str) -> datetime:
        value = datetime.fromisoformat(str(source[key]).replace("Z", "+00:00"))
        if value.tzinfo is None:
            raise ValueError(f"{key} must have a timezone")
        value = value.astimezone(timezone.utc)
        if value.second or value.microsecond:
            raise ValueError(f"{key} must be aligned to a whole minute")
        return value

    start_at, stop_at = timestamp("start_at"), timestamp("stop_at")
    if start_at >= stop_at:
        raise ValueError("stop_at must follow start_at")
    local_start = start_at.astimezone(NEW_YORK)
    local_stop = stop_at.astimezone(NEW_YORK)
    if (local_start.date() != local_stop.date()
            or not (9, 30) <= (local_start.hour, local_start.minute)
            or (local_stop.hour, local_stop.minute) > (16, 0)):
        raise ValueError("Live Feed must cover one declared regular New York session")
    history_limit = source.get("history_limit", 390)
    if isinstance(history_limit, bool) or not isinstance(history_limit, int) or history_limit < 2:
        raise ValueError("history_limit must be an integer of at least 2")
    delay = source.get("finalization_delay_seconds", 95)
    if isinstance(delay, bool) or not isinstance(delay, int) or not 95 <= delay < 120:
        raise ValueError("finalization_delay_seconds must be from 95 through 119")
    max_quiet = source.get("max_quiet_minutes", 0)
    if isinstance(max_quiet, bool) or not isinstance(max_quiet, int) or max_quiet < 0:
        raise ValueError("max_quiet_minutes must be a nonnegative integer")
    root = path.resolve().parent
    spec = LiveSpec(
        feed_id=feed_id, start_at=start_at, stop_at=stop_at,
        provider=provider, market_feed=market_feed,
        database_path=str((root / source["database_path"]).resolve()),
        slippage_bps=_finite_nonnegative(source.get("slippage_bps", 0), "slippage_bps"),
        commission_per_order=_finite_nonnegative(source.get("commission_per_order", 0),
                                                "commission_per_order"),
        cells=_cells(source), history_limit=history_limit,
        finalization_delay_seconds=delay, max_quiet_minutes=max_quiet,
    )
    _fixed_universe(spec.cells, "Live Feed")
    if cost_model(source) != CostModel():
        raise ValueError("Per-share and minimum commissions and range slippage are for daily runs; "
                         "Live Feed does not support them yet")
    if any(cell.model_card is not None or cell.ask is not None for cell in spec.cells):
        raise ValueError("Daily model cards and context.ask cannot run on Live Feed minute bars")
    if any(cell.allocator is not None for cell in spec.cells):
        raise ValueError("Allocators run on daily closes; Live Feed does not support them yet")
    if any(cell.ablation for cell in spec.cells):
        raise ValueError("ablation is for Lab variants only")
    if spec.slippage_bps >= 10_000:
        raise ValueError("slippage_bps must be below 10000")
    return spec, raw

