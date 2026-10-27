"""Measure a synthetic multi-cell Feed Batch restart on this machine."""

from __future__ import annotations

import argparse
import gc
import json
import tempfile
import time
import tracemalloc
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from quantarium.feed import run_feed_batch
from quantarium.model import Bar, CellSpec, FeedSpec, StrategySpec


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def _sessions(count: int) -> list[date]:
    days: list[date] = []
    day = date(2024, 1, 2)
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


class SyntheticProvider:
    def __init__(self, days: list[date], symbols: tuple[str, ...]):
        self.tape = {
            day: {
                symbol: Bar(day, symbol, 10 + index / 100, 10 + index / 100,
                            10 + index / 100, 10 + index / 100, 1000)
                for index, symbol in enumerate(symbols)
            }
            for day in days
        }

    def actions(self, symbols: set[str], start: date, through: date) -> list[dict]:
        return []

    def sessions(self, start: date, through: date) -> set[date]:
        return {day for day in self.tape if start <= day <= through}

    def fetch(self, symbols: set[str], start: date, through: date) -> dict[date, dict[str, Bar]]:
        return {day: {symbol: bars[symbol] for symbol in symbols}
                for day, bars in self.tape.items() if start <= day <= through}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", type=int, default=100)
    parser.add_argument("--sessions", type=int, default=80)
    parser.add_argument("--cells", type=int, default=8)
    parser.add_argument("--rebalance-every", type=int, default=21)
    args = parser.parse_args()
    if min(args.symbols, args.sessions, args.cells, args.rebalance_every) < 1:
        parser.error("all counts must be positive")
    symbols = tuple(f"S{index:04d}" for index in range(args.symbols))
    days = _sessions(args.sessions)
    provider = SyntheticProvider(days, symbols)
    cells = tuple(
        CellSpec(f"basket_{index}", "complex", symbols, 100_000,
                 (StrategySpec("strategies:EqualWeight", 1, {}),),
                 args.rebalance_every)
        for index in range(args.cells)
    )
    now = datetime.combine(days[-1] + timedelta(days=3), datetime.min.time(), timezone.utc)
    runs = ROOT / "runs"
    runs.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="quantarium-feed-scale-", dir=runs) as temp:
        database = Path(temp) / "feed.sqlite"
        spec = FeedSpec("scale_probe", days[0], "alpaca", "iex", str(database),
                        0, 0, cells)
        first = run_feed_batch(spec, "{}", EXAMPLES, provider, through=days[-1], now=now)
        if first["days_processed_this_call"] != args.sessions:
            raise AssertionError("Initial batch did not save every synthetic session")
        gc.collect()
        tracemalloc.start()
        started = time.perf_counter()
        resumed = run_feed_batch(spec, "{}", EXAMPLES, provider, through=days[-1], now=now)
        restart_seconds = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        if resumed["days_processed_this_call"] != 0:
            raise AssertionError("Restart unexpectedly advanced the synthetic feed")
        print(json.dumps({
            "symbols": args.symbols,
            "sessions": args.sessions,
            "cells": args.cells,
            "bars_saved": resumed["bars_cached"],
            "restart_seconds": round(restart_seconds, 3),
            "restart_peak_traced_python_mib": round(peak / 1024**2, 2),
            "database_mib": round(database.stat().st_size / 1024**2, 2),
        }, indent=2))


if __name__ == "__main__":
    main()
