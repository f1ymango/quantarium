"""Measure a synthetic multi-stock Static replay on this machine."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
import tracemalloc
from datetime import date, timedelta
from pathlib import Path

from barloom.engine import run_static
from benchmarks.process_memory import process_memory_mib
from barloom.journal import read_report
from barloom.model import CellSpec, RunSpec, StrategySpec


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def _sessions(count: int) -> list[date]:
    days = []
    day = date(2024, 1, 2)
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", type=int, default=500)
    parser.add_argument("--sessions", type=int, default=252)
    parser.add_argument("--rebalance-every", type=int, default=21)
    parser.add_argument("--trace-memory", action="store_true",
                        help="also report Python's traced peak; this slows the replay several times over")
    args = parser.parse_args()
    if min(args.symbols, args.sessions, args.rebalance_every) < 1:
        parser.error("all counts must be positive")
    symbols = tuple(f"S{index:04d}" for index in range(args.symbols))
    days = _sessions(args.sessions)
    runs = ROOT / "runs"
    runs.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="barloom-scale-", dir=runs) as temp:
        folder = Path(temp)
        tape = folder / "prices.csv"
        start = time.perf_counter()
        with tape.open("w", encoding="utf-8", newline="") as target:
            target.write("date,symbol,open,high,low,close,volume\n")
            for day_index, day in enumerate(days):
                for symbol_index, symbol in enumerate(symbols):
                    price = round(10 + symbol_index * 0.01 + day_index * 0.001, 3)
                    target.write(f"{day},{symbol},{price},{price},{price},{price},1000\n")
        generation_seconds = time.perf_counter() - start
        tape_bytes = tape.stat().st_size
        cell = CellSpec(
            "equal_weight", "complex", symbols, 1_000_000,
            (StrategySpec("strategies:EqualWeight", 1, {}),),
            args.rebalance_every,
        )
        database = folder / "results.sqlite"
        spec = RunSpec(str(tape), str(database), 0, 0, (cell,))
        if args.trace_memory:
            tracemalloc.start()
        start = time.perf_counter()
        run_id = run_static(spec, "{}", EXAMPLES)
        replay_seconds = time.perf_counter() - start
        peak = tracemalloc.get_traced_memory()[1] if args.trace_memory else None
        tracemalloc.stop()
        result = read_report(database, run_id)
        print(json.dumps({
            "symbols": args.symbols,
            "sessions": args.sessions,
            "bars": args.symbols * args.sessions,
            "rebalance_every_sessions": args.rebalance_every,
            "csv_mib": round(tape_bytes / 1024**2, 2),
            "database_mib": round(database.stat().st_size / 1024**2, 2),
            "generation_seconds": round(generation_seconds, 3),
            "replay_seconds": round(replay_seconds, 3),
            "replay_microseconds_per_bar": round(replay_seconds * 1e6 / (args.symbols * args.sessions), 1),
            "peak_traced_python_mib": None if peak is None else round(peak / 1024**2, 2),
            **process_memory_mib(),
            "status": result["status"],
        }, indent=2))


if __name__ == "__main__":
    main()
