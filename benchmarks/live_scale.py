"""Synthetic Live Feed scale probe without market-data credentials."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
import tracemalloc
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quantarium.live import LiveFeed
from quantarium.model import CellSpec, LiveSpec, MinuteBar, StrategySpec
from benchmarks.process_memory import process_memory_mib


ROOT = Path(__file__).resolve().parents[1]
START = datetime(2024, 1, 2, 14, 30, tzinfo=timezone.utc)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", type=int, default=100)
    parser.add_argument("--minutes", type=int, default=30)
    parser.add_argument("--cells", type=int, default=8)
    parser.add_argument("--history-limit", type=int, default=30)
    args = parser.parse_args()
    if not 1 <= args.symbols or not 1 <= args.cells or not 1 <= args.minutes <= 390:
        parser.error("symbols and cells must be positive; minutes must be from 1 through 390")
    if args.history_limit < 2:
        parser.error("history-limit must be at least 2")
    symbols = tuple(f"S{number:04d}" for number in range(args.symbols))
    cells = tuple(CellSpec(
        f"cell_{number}", "complex", symbols, 100_000,
        (StrategySpec("strategies:EqualWeight", 1, {}),),
    ) for number in range(args.cells))
    (ROOT / "runs").mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / "runs") as temp:
        database = Path(temp) / "live.sqlite"
        spec = LiveSpec(
            "scale_probe", START, START + timedelta(minutes=args.minutes),
            "alpaca", "iex", str(database), 0, 0, cells,
            history_limit=args.history_limit,
        )
        config_json = json.dumps({
            "symbols": args.symbols, "minutes": args.minutes,
            "cells": args.cells, "history_limit": args.history_limit,
        })
        tracemalloc.start()
        started = time.perf_counter()
        feed = LiveFeed(spec, config_json, ROOT / "examples")
        try:
            for minute in range(args.minutes):
                stamp = START + timedelta(minutes=minute)
                observed_at = stamp + timedelta(seconds=61)
                for number, symbol in enumerate(symbols):
                    price = 10 + number / 10 + minute / 100
                    feed.accept(MinuteBar(
                        stamp, symbol, price, price + 0.02, price - 0.02,
                        price, 1000,
                    ), observed_at)
                feed.flush(stamp + timedelta(seconds=95))
            report = feed.report()
        finally:
            feed.close()
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        print(json.dumps({
            "symbols": args.symbols, "minutes": args.minutes,
            "cells": args.cells, "history_limit": args.history_limit,
            "elapsed_seconds": round(elapsed, 3),
            "peak_traced_python_mib": round(peak / 2**20, 3),
            **process_memory_mib(),
            "database_mib": round(database.stat().st_size / 2**20, 3),
            "bars_saved": report["bars_cached"],
            "status": report["status"],
        }, indent=2))


if __name__ == "__main__":
    main()
