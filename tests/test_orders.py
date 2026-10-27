"""Starting holdings, cost basis, and limit and stop orders on daily bars."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import MappingProxyType

from quantarium.config import _cells
from quantarium.engine import CellRuntime, _validate_weights
from quantarium.model import Bar, CellSpec, CorporateAction, Decision, RunSpec, StrategySpec

STRATEGIES = '''
from quantarium.model import Decision, Order


class Protect:
    """Place a stop and a limit once, then keep them, reporting cost basis."""

    def __init__(self, stop=95.0, limit=110.0, shares=10, expires=None):
        self.stop, self.limit, self.shares, self.expires, self.placed = stop, limit, shares, expires, False

    def on_bar(self, context):
        notes = {"cost": context.cost_basis.get("AAA"), "open_orders": len(context.open_orders)}
        if self.placed:
            return Decision(notes=notes)
        self.placed = True
        orders = []
        if self.stop:
            orders.append(Order("AAA", "sell", "stop", self.shares, self.stop, self.expires, "stop"))
        if self.limit:
            orders.append(Order("AAA", "sell", "limit", self.shares, self.limit, self.expires, "take_profit"))
        return Decision(orders=tuple(orders), notes=notes)


class BuyDip:
    def __init__(self):
        self.placed = False

    def on_bar(self, context):
        if self.placed:
            return Decision(notes={"cost": context.cost_basis.get("AAA")})
        self.placed = True
        return Decision(orders=(Order("AAA", "buy", "limit", 10, 95.0),))


class BuyTwo:
    def __init__(self):
        self.placed = False

    def on_bar(self, context):
        if self.placed:
            return Decision()
        self.placed = True
        return Decision(orders=(Order("AAA", "buy", "limit", 5, 100.0),
                                Order("AAA", "buy", "limit", 5, 100.0)))


class BuyMarket:
    def __init__(self):
        self.placed = False

    def on_bar(self, context):
        if self.placed:
            return Decision()
        self.placed = True
        return Decision(orders=(Order("AAA", "buy", "market", 10, 1.0),))


class AllIn:
    def on_bar(self, context):
        return 1.0


class TrimNow:
    def __init__(self):
        self.done = False

    def on_bar(self, context):
        if self.done:
            return Decision()
        self.done = True
        return Decision(orders=(Order("AAA", "sell", "market", 4, 1.0, tag="trim"),))
'''


class Journal:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def event(self, cell: str, day: str, kind: str, payload: dict) -> None:
        self.events.append((day, kind, json.loads(json.dumps(payload))))

    def kinds(self, kind: str) -> list[tuple[str, dict]]:
        return [(day, payload) for day, k, payload in self.events if k == kind]


def bar(day: int, open_: float, high: float, low: float, close: float) -> Bar:
    return Bar(date(2024, 1, day), "AAA", open_, high, low, close, 10_000)


class OrderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "strats.py").write_text(STRATEGIES, encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def runtime(self, entry: str, holdings=None, cash: float = 2000.0, parameters=None,
                slippage=0.0, participation=None):
        cell = CellSpec("c", "simple", ("AAA",), cash, (StrategySpec(f"strats:{entry}", 1, parameters or {}),),
                        max_previous_volume_participation=participation,
                        initial_positions=MappingProxyType(holdings or {}))
        spec = RunSpec("", "", slippage, 1.0, (cell,))
        return CellRuntime(cell, spec, self.root)

    def play(self, runtime: CellRuntime, bars: list[Bar]) -> Journal:
        journal = Journal()
        for bar_ in bars:
            runtime.process_day(bar_.date, {"AAA": bar_}, journal, True)
        return journal

    def test_starting_holding_is_bought_at_the_first_open_and_keeps_the_users_cost(self) -> None:
        runtime = self.runtime("Protect", {"AAA": (10, 80.0)}, parameters={"stop": 0, "limit": 0})
        journal = self.play(runtime, [bar(2, 100, 101, 99, 100), bar(3, 100, 101, 99, 100)])
        (day, order), = journal.kinds("order")
        self.assertEqual((day, order["filled_shares"], order["fill_price"], order["reason"]),
                         ("2024-01-02", 10, 100, "starting_holding"))
        self.assertAlmostEqual(runtime.cash, 2000 - 1000 - 1)
        self.assertEqual(runtime.positions["AAA"], 10)
        notes = [p["decisions"][0]["notes"] for _, p in journal.kinds("signal")]
        self.assertEqual(notes[0]["cost"], 80.0)
        self.assertIsNone(journal.kinds("signal")[0][1]["target_weights"])  # holding, not rebalancing

    def test_starting_holdings_need_enough_cash(self) -> None:
        runtime = self.runtime("Protect", {"AAA": (30, 80.0)}, cash=1000)
        with self.assertRaisesRegex(ValueError, "cannot buy the starting"):
            self.play(runtime, [bar(2, 100, 101, 99, 100)])

    def test_orders_wait_for_the_next_session_and_a_stop_fills_at_its_price(self) -> None:
        runtime = self.runtime("Protect", {"AAA": (10, 80.0)})
        # Day 2's own range would hit both orders, but they are placed after its close.
        journal = self.play(runtime, [bar(2, 100, 120, 90, 100), bar(3, 100, 101, 96, 98),
                                     bar(4, 97, 98, 94, 95)])
        fills = [(day, p["order_type"], p["filled_shares"], p["fill_price"]) for day, p in journal.kinds("order")
                 if p.get("order_type")]
        self.assertEqual(fills, [("2024-01-04", "stop", 10, 95.0)])
        self.assertEqual([(d, p["tag"], p["reason"]) for d, p in journal.kinds("order_cancelled")],
                         [("2024-01-04", "take_profit", "no_shares_held")])
        self.assertEqual(runtime.positions["AAA"], 0)
        self.assertEqual(runtime.open_orders, [])
        self.assertNotIn("AAA", runtime.cost_basis)

    def test_a_gap_through_the_stop_fills_at_the_open(self) -> None:
        runtime = self.runtime("Protect", {"AAA": (10, 80.0)}, parameters={"limit": 0})
        journal = self.play(runtime, [bar(2, 100, 101, 99, 100), bar(3, 90, 92, 88, 91)])
        (_, fill), = [(d, p) for d, p in journal.kinds("order") if p.get("order_type")]
        self.assertEqual(fill["fill_price"], 90)

    def test_a_day_touching_both_prices_assumes_the_stop_came_first(self) -> None:
        runtime = self.runtime("Protect", {"AAA": (10, 80.0)})
        journal = self.play(runtime, [bar(2, 100, 101, 99, 100), bar(3, 100, 112, 94, 105)])
        fills = [(p["order_type"], p["filled_shares"]) for _, p in journal.kinds("order") if p.get("order_type")]
        self.assertEqual(fills, [("stop", 10), ("limit", 0)])

    def test_a_triggered_stop_pays_slippage_but_a_limit_does_not(self) -> None:
        runtime = self.runtime("Protect", {"AAA": (10, 80.0)}, parameters={"stop": 0}, slippage=100)
        journal = self.play(runtime, [bar(2, 100, 101, 99, 100), bar(3, 105, 111, 104, 110)])
        fill = [p for _, p in journal.kinds("order") if p.get("order_type")][0]
        self.assertEqual(fill["fill_price"], 110.0)
        runtime = self.runtime("Protect", {"AAA": (10, 80.0)}, parameters={"limit": 0}, slippage=100)
        journal = self.play(runtime, [bar(2, 100, 101, 99, 100), bar(3, 97, 98, 94, 96)])
        fill = [p for _, p in journal.kinds("order") if p.get("order_type")][0]
        self.assertAlmostEqual(fill["fill_price"], 95 * 0.99)

    def test_a_buy_limit_sets_the_cost_basis_from_its_fill(self) -> None:
        runtime = self.runtime("BuyDip")
        journal = self.play(runtime, [bar(2, 100, 101, 99, 100), bar(3, 99, 100, 94, 97), bar(4, 97, 98, 96, 97)])
        self.assertEqual(runtime.positions["AAA"], 10)
        self.assertEqual(runtime.cost_basis["AAA"], 95.0)
        self.assertEqual(journal.kinds("signal")[-1][1]["decisions"][0]["notes"]["cost"], 95.0)

    def test_partial_fills_rest_and_share_one_daily_volume_cap(self) -> None:
        runtime = self.runtime("BuyTwo", cash=5000, participation=0.00031)
        journal = self.play(runtime, [bar(2, 100, 101, 99, 100),
                                      bar(3, 100, 101, 99, 100),
                                      bar(4, 100, 101, 99, 100)])
        by_day = {day: [payload["filled_shares"] for d, payload in journal.kinds("order")
                        if d == day and payload.get("order_type") == "limit"]
                  for day in ("2024-01-03", "2024-01-04")}
        self.assertEqual(by_day["2024-01-03"], [3, 0])
        self.assertEqual(by_day["2024-01-04"], [2, 1])
        self.assertEqual(runtime.positions["AAA"], 6)
        self.assertEqual([order["shares"] for order in runtime.open_orders], [4])

    def test_a_market_order_fills_at_the_next_open_with_slippage(self) -> None:
        runtime = self.runtime("TrimNow", {"AAA": (10, 80.0)}, slippage=100)
        journal = self.play(runtime, [bar(2, 100, 101, 99, 100), bar(3, 90, 120, 85, 110)])
        fill = [p for _, p in journal.kinds("order") if p.get("order_type")][0]
        self.assertEqual((fill["filled_shares"], fill["fill_price"]), (4, 90 * 0.99))
        self.assertEqual(runtime.positions["AAA"], 6)
        self.assertEqual(runtime.cost_basis["AAA"], 80.0)  # selling part keeps the cost basis

    def test_unfilled_market_order_does_not_trade_on_a_later_session(self) -> None:
        runtime = self.runtime("BuyMarket", cash=100)
        journal = self.play(runtime, [bar(2, 100, 101, 99, 100),
                                      bar(3, 200, 201, 199, 200),
                                      bar(4, 1, 2, 1, 1)])
        self.assertEqual(runtime.positions["AAA"], 0)
        self.assertEqual(runtime.open_orders, [])
        cancellations = journal.kinds("order_cancelled")
        self.assertEqual([(day, payload["reason"]) for day, payload in cancellations],
                         [("2024-01-03", "market_session_complete")])

    def test_unfilled_orders_expire_after_their_sessions(self) -> None:
        runtime = self.runtime("Protect", {"AAA": (10, 80.0)}, parameters={"expires": 2})
        journal = self.play(runtime, [bar(d, 100, 101, 99, 100) for d in (2, 3, 4, 5)])
        self.assertEqual([day for day, _ in journal.kinds("order_expired")], ["2024-01-04", "2024-01-04"])
        self.assertEqual(runtime.open_orders, [])

    def test_open_orders_and_cost_basis_survive_a_checkpoint(self) -> None:
        runtime = self.runtime("Protect", {"AAA": (10, 80.0)})
        self.play(runtime, [bar(2, 100, 101, 99, 100)])
        saved = json.loads(json.dumps(runtime.checkpoint()))
        self.assertEqual(saved["cost_basis"], {"AAA": 80.0})
        self.assertEqual([o["kind"] for o in saved["open_orders"]], ["stop", "limit"])
        restored = self.runtime("Protect", {"AAA": (10, 80.0)})
        restored.restore_checkpoint(saved, {"AAA": [bar(2, 100, 101, 99, 100)]})
        self.assertEqual(restored.open_orders, runtime.open_orders)
        self.assertTrue(restored.initial_done)
        journal = Journal()
        restored.process_day(date(2024, 1, 3), {"AAA": bar(3, 96, 97, 94, 95)}, journal, True)
        self.assertEqual(restored.positions["AAA"], 0)
        self.assertEqual([p["filled_shares"] for _, p in journal.kinds("order")], [10])
        self.assertEqual(len(journal.kinds("order_cancelled")), 1)
        # The test strategy keeps no state of its own, so after a restart it places new orders.
        self.assertEqual([o["id"] for o in restored.open_orders], [3, 4])

    def test_split_adjusts_cost_basis_and_resting_order_terms(self) -> None:
        runtime = self.runtime("Protect", {"AAA": (10, 80.0)})
        journal = Journal()
        runtime.process_day(date(2024, 1, 2), {"AAA": bar(2, 100, 101, 99, 100)}, journal, True)
        split = CorporateAction(date(2024, 1, 3), "AAA", "split", 2, date(2024, 1, 3))
        runtime.process_day(date(2024, 1, 3), {"AAA": bar(3, 50, 51, 49, 50)}, journal, True,
                            (split,))
        self.assertEqual(runtime.positions["AAA"], 20)
        self.assertEqual(runtime.cost_basis["AAA"], 40.0)
        self.assertEqual([(o["kind"], o["shares"], o["price"]) for o in runtime.open_orders],
                         [("stop", 20, 47.5), ("limit", 20, 55.0)])
        self.assertEqual(len(journal.kinds("order_adjusted")), 2)
        runtime.process_day(date(2024, 1, 4), {"AAA": bar(4, 48, 49, 47, 48)}, journal, True)
        fills = [p for _, p in journal.kinds("order") if p.get("order_type") == "stop"]
        self.assertEqual((fills[0]["filled_shares"], fills[0]["fill_price"]), (20, 47.5))

    def test_plain_weight_strategies_keep_their_checkpoint_shape(self) -> None:
        runtime = self.runtime("AllIn")
        self.play(runtime, [bar(2, 100, 101, 99, 100), bar(3, 100, 101, 99, 100)])
        self.assertEqual(runtime.cost_basis["AAA"], 100)
        self.assertNotIn("open_orders", runtime.checkpoint())
        self.assertNotIn("initial_done", runtime.checkpoint())

    def test_live_feed_refuses_decisions_for_now(self) -> None:
        cell = CellSpec("c", "simple", ("AAA",), 1000, (StrategySpec("x:Y", 1, {}),))
        with self.assertRaisesRegex(ValueError, "Live Feed does not yet"):
            _validate_weights(Decision(weights=1.0), cell)


class HoldingsConfigTests(unittest.TestCase):
    def base(self, **extra) -> dict:
        cell = {"name": "c", "mode": "simple", "symbols": ["aaa"], "initial_cash": 1000,
                "strategies": [{"entrypoint": "s:S"}], **extra}
        return {"cells": [cell]}

    def test_holdings_are_parsed_with_optional_cost(self) -> None:
        cell, = _cells(self.base(initial_positions={"aaa": {"shares": 5, "cost": 12.5}}))
        self.assertEqual(dict(cell.initial_positions), {"AAA": (5, 12.5)})
        cell, = _cells(self.base(initial_positions={"AAA": {"shares": 5}}))
        self.assertEqual(dict(cell.initial_positions), {"AAA": (5, None)})

    def test_bad_holdings_are_refused(self) -> None:
        for bad, message in (({"BBB": {"shares": 5}}, "not a cell symbol"),
                             ({"AAA": {"shares": 0}}, "positive whole number"),
                             ({"AAA": {"shares": 2.5}}, "positive whole number"),
                             ({"AAA": {"shares": 5, "cost": 0}}, "must be positive"),
                             ({"AAA": 5}, "must be an object"),
                             ([{"symbol": "AAA", "shares": 5}], "must be an object"),
                             ({"AAA": {"shares": 1}, "aaa": {"shares": 1}}, "repeats AAA")):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, message):
                _cells(self.base(initial_positions=bad))



class SimpleCellWeightTests(unittest.TestCase):
    def test_a_dict_from_a_simple_cell_gets_a_clear_message(self) -> None:
        from quantarium.config import _cells
        from quantarium.engine import _validate_weights

        cell = _cells({"cells": [{"name": "One", "mode": "simple", "symbols": ["AAA"], "initial_cash": 1000,
                                  "strategies": [{"entrypoint": "x:Y", "allocation": 1}]}]})[0]
        self.assertEqual(_validate_weights(0.5, cell), {"AAA": 0.5})
        with self.assertRaisesRegex(ValueError, "returns one target weight"):
            _validate_weights({"AAA": 1.0}, cell)

if __name__ == "__main__":
    unittest.main()


class EndToEndTests(unittest.TestCase):
    def test_static_run_with_orders_reads_back_through_reports(self) -> None:
        from quantarium.analysis import read_analysis
        from quantarium.engine import run_static
        from quantarium.timeline import read_timeline

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "strats.py").write_text(STRATEGIES, encoding="utf-8")
            rows = [(2, 100, 101, 99, 100), (3, 100, 101, 96, 98), (4, 97, 98, 94, 95), (5, 95, 96, 94, 95)]
            (root / "prices.csv").write_text("date,symbol,open,high,low,close,volume\n" + "".join(
                f"2024-01-0{d},AAA,{o},{h},{l},{c},10000\n" for d, o, h, l, c in rows), encoding="utf-8")
            cell = CellSpec("c", "simple", ("AAA",), 2000.0, (StrategySpec("strats:Protect", 1, {}),),
                            initial_positions=MappingProxyType({"AAA": (10, 80.0)}))
            database = root / "run.sqlite"
            run_id = run_static(RunSpec(str(root / "prices.csv"), str(database), 0, 1.0, (cell,)), "{}", root)
            report = read_analysis(database, "static", run_id)
            result, = report["cells"]
            # Start 2000; buy 10 at 100 (+1 fee); stop sells 10 at 95 (-1 fee): 2000 - 50 - 2.
            self.assertAlmostEqual(result["total_return_pct"], -2.6)
            self.assertEqual((result["orders_filled"], result["commission_total"]), (2, 2.0))
            timeline = read_timeline(database, "static", "c", run_id)
            kinds = {event["kind"] for day in timeline["days"] for event in day["other_events"]}
            self.assertEqual(kinds, {"order_placed", "order_cancelled"})
