"""Per-share and minimum commissions and range slippage (v0.8.2)."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path

from quantarium.config import _cells, cost_model, load_config, load_live_config
from quantarium.engine import _affordable, _commission, _rebalance, _slip, run_static
from quantarium.model import Bar, CostModel
from quantarium.summary import _static_costs

ROOT = Path(__file__).resolve().parents[1]


class Journal:
    def __init__(self):
        self.orders = []

    def event(self, cell, day, kind, payload):
        if kind == "order":
            self.orders.append(payload)


def bar(day: int, symbol: str = "AAA", open_: float = 100.0, high: float = 101.0, low: float = 99.0,
        close: float = 100.0) -> Bar:
    return Bar(date(2024, 1, day), symbol, open_, high, low, close, 1_000_000)


CELL = _cells({"cells": [{"name": "c", "mode": "simple", "symbols": ["AAA"], "initial_cash": 10_000,
                          "strategies": [{"entrypoint": "strategies:BuyAndHold", "allocation": 1}]}]})[0]


class CostHelpersTest(unittest.TestCase):
    def test_defaults_are_the_flat_costs(self):
        costs = CostModel()
        self.assertEqual(_commission(costs, 1.0, 500), 1.0)
        self.assertEqual(_slip(costs, 7.5, bar(2)), 7.5 / 10_000)
        self.assertEqual(_affordable(1_001.0, 10.0, 1.0, costs), 100)

    def test_commission_is_flat_plus_per_share_but_at_least_the_minimum(self):
        costs = CostModel(commission_per_share=0.005, commission_minimum=1.0)
        self.assertEqual(_commission(costs, 0.0, 10), 1.0)
        self.assertAlmostEqual(_commission(costs, 0.0, 1_000), 5.0)
        self.assertAlmostEqual(_commission(costs, 2.0, 1_000), 7.0)

    def test_range_slippage_uses_the_previous_session_only(self):
        costs = CostModel(slippage_range_fraction=0.1)
        previous = bar(2, high=104.0, low=96.0, close=100.0)
        self.assertAlmostEqual(_slip(costs, 5, previous), 5 / 10_000 + 0.1 * 8 / 100)
        self.assertEqual(_slip(costs, 5, None), 5 / 10_000)

    def test_buys_leave_room_for_the_commission(self):
        per_share = CostModel(commission_per_share=0.5)
        # 100 shares at 10 cost 1,000 plus 50 commission; only 95 fit in 1,000.
        self.assertEqual(_affordable(1_000.0, 10.0, 0.0, per_share), 95)
        minimum = CostModel(commission_minimum=5.0)
        self.assertEqual(_affordable(1_000.0, 10.0, 0.0, minimum), 99)
        self.assertEqual(_affordable(3.0, 10.0, 0.0, minimum), 0)


class RebalanceCostsTest(unittest.TestCase):
    def test_a_buy_pays_range_slippage_and_its_commission_fits_the_cash(self):
        journal = Journal()
        costs = CostModel(commission_per_share=0.01, commission_minimum=2.0, slippage_range_fraction=0.25)
        positions = {"AAA": 0}
        cash = _rebalance(CELL, date(2024, 1, 3), {"AAA": bar(3)}, {"AAA": 1.0}, 10_000.0, positions, 0, 0,
                          journal, {}, costs=costs, previous_bars={"AAA": bar(2, high=102.0, low=98.0)})
        order = journal.orders[-1]
        price = 100.0 * (1 + 0.25 * 4 / 100)
        self.assertAlmostEqual(order["fill_price"], price)
        self.assertEqual(order["filled_shares"], 98)  # 99 would need 10,001
        self.assertAlmostEqual(order["commission"], 2.0)
        self.assertAlmostEqual(cash, 10_000 - 98 * price - 2.0)
        self.assertGreaterEqual(cash, 0)

    def test_without_costs_or_previous_bars_nothing_changes(self):
        before, after = Journal(), Journal()
        args = (CELL, date(2024, 1, 3), {"AAA": bar(3)}, {"AAA": 1.0}, 10_000.0)
        plain = _rebalance(*args, {"AAA": 0}, 5, 1.0, before, {})
        same = _rebalance(*args, {"AAA": 0}, 5, 1.0, after, {}, costs=CostModel(),
                          previous_bars={"AAA": bar(2, high=150.0, low=50.0)})
        self.assertEqual(plain, same)
        self.assertEqual(before.orders, after.orders)


class CostConfigTest(unittest.TestCase):
    def test_bad_costs_are_refused(self):
        for source in ({"commission_per_share": -1}, {"commission_minimum": float("nan")},
                       {"slippage_range_fraction": 2}, {"commission_minimum": float("inf")}):
            with self.subTest(source=source), self.assertRaises((ValueError, TypeError)):
                cost_model(source)
        self.assertEqual(cost_model({}), CostModel())

    def test_live_feed_refuses_the_new_costs(self):
        source = json.loads((ROOT / "examples" / "live_experiment.json").read_text(encoding="utf-8"))
        source["commission_per_share"] = 0.01
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "live.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Live Feed"):
                load_live_config(path)

    def test_a_static_run_charges_and_records_the_costs(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            rows = ["date,symbol,open,high,low,close,volume"]
            for day in range(2, 12):
                price = 100 + day
                rows.append(f"2024-01-{day:02d},AAA,{price},{price + 2},{price - 2},{price + 1},100000")
            (folder / "prices.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
            config = {"data_path": "prices.csv", "database_path": "runs.sqlite", "slippage_bps": 0,
                      "commission_per_order": 0, "commission_per_share": 0.01, "commission_minimum": 3.0,
                      "slippage_range_fraction": 0.1, "strategy_root": str(ROOT / "examples"),
                      "cells": [{"name": "hold", "mode": "simple", "symbols": ["AAA"], "initial_cash": 10_000,
                                 "strategies": [{"entrypoint": "strategies:BuyAndHold", "allocation": 1}]}]}
            path = folder / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            spec, raw = load_config(path)
            self.assertEqual(spec.costs, CostModel(0.01, 3.0, 0.1))
            run_id = run_static(spec, raw, ROOT / "examples")
            with closing(sqlite3.connect(folder / "runs.sqlite")) as db:
                orders = [json.loads(row[0]) for row in db.execute(
                    "SELECT payload_json FROM events WHERE run_id=? AND kind='order' ORDER BY sequence", (run_id,))]
            buy = next(order for order in orders if order["filled_shares"])
            # Bought at the second open, after a session whose range was 4 around a close of 103.
            self.assertAlmostEqual(buy["fill_price"], 103 * (1 + 0.1 * 4 / 103))
            self.assertAlmostEqual(buy["commission"], 3.0)
            costs = _static_costs(folder / "runs.sqlite", run_id)
            self.assertEqual(costs["commission_minimum"], 3.0)
            self.assertEqual(costs["slippage_range_fraction"], 0.1)


if __name__ == "__main__":
    unittest.main()
