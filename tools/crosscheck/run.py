"""Cross-check Quantarium's engine against backtrader on shared cases.

Run with Quantarium's own Python, pointing at the reference environment:

    py tools/crosscheck/run.py --reference-python path/to/reference-env/Scripts/python.exe

or set QUANTARIUM_REFERENCE_PYTHON. Each case runs on Quantarium's engine in this
process and on backtrader in a separate process (``backtrader_runner.py``,
started with ``-I`` in a temporary folder and only basic system variables).
The two exchange JSON files only. See docs/plans/v0.8.x-engine.md.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from quantarium.legacy import env
from quantarium.config import load_config, strategy_root  # noqa: E402
from quantarium.engine import run_static  # noqa: E402

RUNNER = Path(__file__).with_name("backtrader_runner.py")
SCENARIOS = ROOT / "examples" / "scenarios"
# Equity may differ by floating-point rounding only; fills must match exactly in shares, and in price to rounding.
EQUITY_TOLERANCE = 1e-7
PRICE_TOLERANCE = 1e-9
BASE_ENV = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH")


def cases() -> list[dict]:
    """Every example strategy on every example scenario, with and without costs, then the harder features."""
    found = []
    for scenario in ("steady_bull", "bull_with_crash", "sideways_chop", "recession_bear"):
        prices = SCENARIOS / scenario / "prices.csv"
        symbols = sorted({line.split(",")[1] for line in prices.read_text(encoding="utf-8").splitlines()[1:]})
        for costs in ({"slippage_bps": 0, "commission_per_order": 0}, {"slippage_bps": 7.5, "commission_per_order": 1.0}):
            label = "with costs" if costs["commission_per_order"] else "no costs"
            found += [
                {"name": f"{scenario} BuyAndHold {label}", "prices": str(prices), "symbols": symbols[:1],
                 "mode": "simple", "strategy": "BuyAndHold", "parameters": {}, "cash": 10_000, **costs},
                {"name": f"{scenario} TrendFollower {label}", "prices": str(prices), "symbols": symbols[:1],
                 "mode": "simple", "strategy": "TrendFollower", "parameters": {"window": 10}, "cash": 10_000, **costs},
                {"name": f"{scenario} EqualWeight {label}", "prices": str(prices), "symbols": symbols,
                 "mode": "complex", "strategy": "EqualWeight", "parameters": {}, "cash": 25_000, **costs},
                {"name": f"{scenario} RecentLeader {label}", "prices": str(prices), "symbols": symbols,
                 "mode": "complex", "strategy": "RecentLeader", "parameters": {}, "cash": 25_000, **costs},
            ]
        costs = {"slippage_bps": 7.5, "commission_per_order": 1.0}
        found += [
            # Deciding only every fifth session.
            {"name": f"{scenario} EqualWeight every 5 sessions", "prices": str(prices), "symbols": symbols,
             "mode": "complex", "strategy": "EqualWeight", "parameters": {}, "cash": 25_000, "rebalance_every": 5,
             **costs},
            {"name": f"{scenario} TrendFollower every 5 sessions", "prices": str(prices), "symbols": symbols[:1],
             "mode": "simple", "strategy": "TrendFollower", "parameters": {"window": 10}, "cash": 10_000,
             "rebalance_every": 5, **costs},
            # A volume cap tight enough to bind: a large account trading a small share of yesterday's volume.
            {"name": f"{scenario} RecentLeader volume cap", "prices": str(prices), "symbols": symbols,
             "mode": "complex", "strategy": "RecentLeader", "parameters": {}, "cash": 2_000_000,
             "volume_cap": 0.002, **costs},
            # Starting holdings, bought at the first open.
            {"name": f"{scenario} EqualWeight starting holdings", "prices": str(prices), "symbols": symbols,
             "mode": "complex", "strategy": "EqualWeight", "parameters": {}, "cash": 25_000,
             "initial_positions": {symbols[0]: 100}, **costs},
            # Resting stop and limit orders.
            {"name": f"{scenario} StopGuard", "prices": str(prices), "symbols": symbols[:1], "mode": "simple",
             "strategy": "StopGuard", "parameters": {}, "cash": 10_000, "root": "crosscheck", **costs},
            {"name": f"{scenario} DipBuyer", "prices": str(prices), "symbols": symbols[:1], "mode": "simple",
             "strategy": "DipBuyer", "parameters": {}, "cash": 10_000, "root": "crosscheck", **costs},
        ]
        # A commission per share with a minimum, as many brokers charge, and no flat part.
        per_share = {"slippage_bps": 5, "commission_per_order": 0, "commission_per_share": 0.005,
                     "commission_minimum": 1.0}
        found += [
            {"name": f"{scenario} EqualWeight per-share commission", "prices": str(prices), "symbols": symbols,
             "mode": "complex", "strategy": "EqualWeight", "parameters": {}, "cash": 25_000, **per_share},
            {"name": f"{scenario} RecentLeader per-share commission", "prices": str(prices), "symbols": symbols,
             "mode": "complex", "strategy": "RecentLeader", "parameters": {}, "cash": 250_000, **per_share},
            {"name": f"{scenario} DipBuyer per-share commission", "prices": str(prices), "symbols": symbols[:1],
             "mode": "simple", "strategy": "DipBuyer", "parameters": {}, "cash": 10_000, "root": "crosscheck",
             **per_share},
        ]
    return found


def run_quantarium(case: dict, folder: Path) -> dict:
    """The case on Quantarium's engine: daily equity at each close, and every fill."""
    module = "crosscheck_strategies" if case.get("root") == "crosscheck" else "strategies"
    cell = {"name": "case", "mode": case["mode"], "symbols": case["symbols"], "initial_cash": case["cash"],
            "strategies": [{"entrypoint": f"{module}:{case['strategy']}", "allocation": 1,
                            "parameters": case["parameters"]}],
            "rebalance_every_sessions": case.get("rebalance_every", 1)}
    if case.get("volume_cap") is not None:
        cell["max_previous_volume_participation"] = case["volume_cap"]
    if case.get("initial_positions"):
        cell["initial_positions"] = {symbol: {"shares": shares} for symbol, shares in case["initial_positions"].items()}
    config = {"data_path": case["prices"], "database_path": str(folder / "quantarium.sqlite"),
              "slippage_bps": case["slippage_bps"], "commission_per_order": case["commission_per_order"],
              **{name: case[name] for name in ("commission_per_share", "commission_minimum") if name in case},
              "strategy_root": str(Path(__file__).parent if module == "crosscheck_strategies" else ROOT / "examples"),
              "cells": [cell]}
    path = folder / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    spec, raw = load_config(path)
    run_id = run_static(spec, raw, strategy_root(path, raw))
    with closing(sqlite3.connect(folder / "quantarium.sqlite")) as db:
        status = db.execute("SELECT status, error FROM cells WHERE run_id=? AND name='case'", (run_id,)).fetchone()
        if status is None or status[0] != "completed":
            raise RuntimeError(f"Quantarium's run failed: {status}")
        equity = [[day, json.loads(payload)["equity"]] for day, payload in db.execute(
            "SELECT date, payload_json FROM events WHERE run_id=? AND kind='valuation' ORDER BY sequence", (run_id,))]
        fills = []
        for day, payload in db.execute(
                "SELECT date, payload_json FROM events WHERE run_id=? AND kind='order' ORDER BY sequence", (run_id,)):
            order = json.loads(payload)
            if order.get("filled_shares"):
                fills.append([day, order["symbol"], order["side"], order["filled_shares"], order["fill_price"]])
    return {"engine": "quantarium", "equity": equity, "fills": fills}


def run_reference(case: dict, folder: Path, python: str) -> dict:
    case_path, result_path = folder / "case.json", folder / "reference.json"
    case_path.write_text(json.dumps(case), encoding="utf-8")
    environment = {key: value for key, value in os.environ.items() if key.upper() in BASE_ENV}
    completed = subprocess.run([python, "-I", str(RUNNER), str(case_path), str(result_path)], cwd=folder,
                               env=environment, capture_output=True, text=True, timeout=600)
    if completed.returncode:
        raise RuntimeError(f"The reference engine failed: {completed.stderr[-2000:]}")
    return json.loads(result_path.read_text(encoding="utf-8"))


def compare(ours: dict, theirs: dict) -> dict:
    """Where two runs of a case differ: fills by date, symbol, side, shares, and price; then daily equity."""
    differences = []
    our_fills = sorted(tuple(fill[:4]) for fill in ours["fills"])
    their_fills = sorted(tuple(fill[:4]) for fill in theirs["fills"])
    missing = sorted(set(our_fills) - set(their_fills))
    extra = sorted(set(their_fills) - set(our_fills))
    if missing:
        differences.append({"kind": "fills only in Quantarium", "count": len(missing), "first": list(missing[0])})
    if extra:
        differences.append({"kind": "fills only in the reference", "count": len(extra), "first": list(extra[0])})
    prices = {tuple(fill[:4]): fill[4] for fill in theirs["fills"]}
    for fill in ours["fills"]:
        other = prices.get(tuple(fill[:4]))
        if other is not None and abs(fill[4] - other) > PRICE_TOLERANCE * max(1.0, abs(other)):
            differences.append({"kind": "fill price", "fill": fill[:4], "quantarium": fill[4], "reference": other})
            break
    theirs_by_day = dict((day, value) for day, value in theirs["equity"])
    worst = None
    for day, value in ours["equity"]:
        other = theirs_by_day.get(day)
        if other is None:
            differences.append({"kind": "equity day only in Quantarium", "day": day})
            break
        gap = abs(value - other) / max(1.0, abs(other))
        if worst is None or gap > worst[0]:
            worst = (gap, day, value, other)
    if len(theirs["equity"]) != len(ours["equity"]):
        differences.append({"kind": "different number of days", "quantarium": len(ours["equity"]),
                            "reference": len(theirs["equity"])})
    if worst and worst[0] > EQUITY_TOLERANCE:
        differences.append({"kind": "equity", "worst_relative_gap": worst[0], "day": worst[1], "quantarium": worst[2],
                            "reference": worst[3]})
    return {"match": not differences, "fills": len(ours["fills"]), "days": len(ours["equity"]),
            "worst_equity_gap": worst[0] if worst else None, "differences": differences}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reference-python", default=env("REFERENCE_PYTHON"))
    parser.add_argument("--only", help="Run only cases whose name contains this text")
    parser.add_argument("--output", type=Path, help="Write the full report as JSON here")
    args = parser.parse_args()
    if not args.reference_python:
        parser.error("Give --reference-python or set QUANTARIUM_REFERENCE_PYTHON")
    report = []
    for case in cases():
        if args.only and args.only not in case["name"]:
            continue
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            result = compare(run_quantarium(case, folder), run_reference(case, folder, args.reference_python))
        report.append({"case": case["name"], **result})
        mark = "match" if result["match"] else "DIFFERS"
        print(f"{mark:8} {case['name']}: {result['fills']} fills, {result['days']} days"
              + ("" if result["match"] else f" -> {result['differences'][0]}"))
    if args.output:
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    matched = sum(item["match"] for item in report)
    print(f"{matched} of {len(report)} cases match")
    return 0 if matched == len(report) else 1


if __name__ == "__main__":
    sys.exit(main())
