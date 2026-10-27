"""Corporate-action accounting and Feed batch safety checks."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from quantarium.config import load_feed_config
from quantarium.engine import CellRuntime, run_static
from quantarium.feed import read_feed_report, run_feed_batch
from quantarium.journal import read_report
from quantarium.model import (Bar, BarHistory, CellSpec, CorporateAction, FeedSpec,
                           RunSpec, StrategySpec)
from quantarium.provider import AlpacaDailyProvider


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)


class _ListJournal:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def event(self, cell_name: str, day: str, kind: str, payload: dict) -> None:
        self.events.append((kind, payload))


class ActionTests(unittest.TestCase):
    def test_static_split_and_dividend_preserve_account_value(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            folder = Path(temp)
            prices = folder / "prices.csv"
            actions = folder / "actions.csv"
            prices.write_text(
                "date,symbol,open,high,low,close,volume\n"
                "2024-01-02,AAA,100,100,100,100,1000\n"
                "2024-01-03,AAA,100,100,100,100,1000\n"
                "2024-01-04,AAA,50,50,50,50,1000\n"
                "2024-01-05,AAA,49,49,49,49,1000\n"
                "2024-01-08,AAA,49,49,49,49,1000\n", encoding="utf-8",
            )
            actions.write_text(
                "date,symbol,kind,value,ex_date\n"
                "2024-01-04,AAA,split,2,2024-01-04\n"
                "2024-01-08,AAA,dividend,1,2024-01-05\n", encoding="utf-8",
            )
            cell = CellSpec("buy_hold", "simple", ("AAA",), 1000,
                            (StrategySpec("strategies:BuyAndHold", 1, {}),))
            database = folder / "static.sqlite"
            spec = RunSpec(str(prices), str(database), 0, 0, (cell,), str(actions))
            run_id = run_static(spec, "{}", EXAMPLES)
            report = read_report(database, run_id)
            self.assertEqual(report["cells"][0]["final_equity"], 1000)
            with closing(sqlite3.connect(database)) as connection:
                records = [json.loads(row[0]) for row in connection.execute(
                    "SELECT payload_json FROM events WHERE run_id=? AND kind='corporate_action' "
                    "ORDER BY date", (run_id,),
                )]
                valuations = {day: json.loads(payload) for day, payload in connection.execute(
                    "SELECT date, payload_json FROM events WHERE run_id=? AND kind='valuation'",
                    (run_id,),
                )}
            self.assertEqual([record["kind"] for record in records],
                             ["split", "dividend_entitlement", "dividend_payment"])
            self.assertEqual(records[0]["shares_before"], 10)
            self.assertEqual(records[0]["shares_after"], 20)
            self.assertEqual(records[1]["eligible_shares"], 20)
            self.assertEqual(records[1]["receivable"], 20)
            self.assertEqual(records[2]["cash_paid"], 20)
            self.assertEqual(valuations["2024-01-05"]["equity"], 1000)
            self.assertEqual(valuations["2024-01-05"]["receivables"], {"AAA:2024-01-08": 20})
            self.assertEqual(valuations["2024-01-08"]["cash"], 20)

    def test_bar_history_adjusts_only_bars_before_each_split(self) -> None:
        bars = [Bar(date(2024, 1, day), "AAA", 120, 120, 120, 120, 100)
                for day in (2, 3, 4)]
        current = Bar(date(2024, 1, 5), "AAA", 20, 20, 20, 20, 600)
        history = BarHistory(bars, current, [(date(2024, 1, 3), 2), (date(2024, 1, 5), 3)])
        self.assertEqual([bar.close for bar in history], [20, 40, 40, 20])
        self.assertEqual(history[0].volume, 600)
        self.assertIs(history[-1], current)
        self.assertIs(BarHistory(bars, current)[0], bars[0])

    def test_strategy_history_is_split_adjusted_in_static_and_after_restore(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            folder = Path(temp)
            (folder / "momentum.py").write_text(
                "class StayIfNotCrashed:\n"
                "    def on_bar(self, context):\n"
                "        h = context.history['AAA']\n"
                "        if len(h) < 2:\n"
                "            return 1.0\n"
                "        return 1.0 if h[-1].close / h[-2].close > 0.75 else 0.0\n",
                encoding="utf-8",
            )
            prices = folder / "prices.csv"
            actions = folder / "actions.csv"
            prices.write_text(
                "date,symbol,open,high,low,close,volume\n"
                "2024-01-02,AAA,100,100,100,100,1000\n"
                "2024-01-03,AAA,100,100,100,100,1000\n"
                "2024-01-04,AAA,50,50,50,50,2000\n"
                "2024-01-05,AAA,49,49,49,49,2000\n", encoding="utf-8",
            )
            actions.write_text(
                "date,symbol,kind,value,ex_date\n"
                "2024-01-04,AAA,split,2,2024-01-04\n", encoding="utf-8",
            )
            cell = CellSpec("momentum", "simple", ("AAA",), 1000,
                            (StrategySpec("momentum:StayIfNotCrashed", 1, {}),),
                            max_previous_volume_participation=0.001)
            database = folder / "static.sqlite"
            spec = RunSpec(str(prices), str(database), 0, 0, (cell,), str(actions))
            run_id = run_static(spec, "{}", folder)
            with closing(sqlite3.connect(database)) as connection:
                signals = [json.loads(row[0])["target_weights"] for row in connection.execute(
                    "SELECT payload_json FROM events WHERE run_id=? AND kind='signal' "
                    "ORDER BY date", (run_id,),
                )]
                orders = {day: json.loads(payload) for day, payload in connection.execute(
                    "SELECT date, payload_json FROM events WHERE run_id=? AND kind='order'",
                    (run_id,),
                )}
            # A raw view would see 50 / 100 = 0.5 on the split day and sell.
            self.assertEqual([weights["AAA"] for weights in signals], [1.0, 1.0, 1.0, 1.0])
            # Yesterday's 1,000 shares are 2,000 in today's units.
            self.assertEqual(orders["2024-01-03"]["previous_volume_cap_shares"], 1)
            self.assertEqual(orders["2024-01-04"]["previous_volume_cap_shares"], 2)

            runtime = CellRuntime(cell, spec, folder)
            journal = _ListJournal()
            split = CorporateAction(date(2024, 1, 4), "AAA", "split", 2, date(2024, 1, 4))
            for day, price in ((2, 100), (3, 100), (4, 50)):
                bar = Bar(date(2024, 1, day), "AAA", price, price, price, price, 1000)
                runtime.process_day(bar.date, {"AAA": bar}, journal, True,
                                    (split,) if bar.date == split.date else ())
            saved = json.loads(json.dumps(runtime.checkpoint()))
            self.assertEqual(saved["splits"], {"AAA": [["2024-01-04", 2]]})
            restored = CellRuntime(cell, spec, folder, saved=saved,
                                   prior_history={"AAA": list(runtime.history["AAA"])})
            self.assertEqual(restored.splits, {"AAA": [(date(2024, 1, 4), 2)]})
            restored.process_day(date(2024, 1, 5), {"AAA": Bar(
                date(2024, 1, 5), "AAA", 49, 49, 49, 49, 1000)}, journal, True)
            signal = [payload for kind, payload in journal.events if kind == "signal"][-1]
            self.assertEqual(signal["target_weights"], {"AAA": 1.0})
            without_splits = dict(saved)
            del without_splits["splits"]
            legacy = CellRuntime(cell, spec, folder, saved=without_splits,
                                 prior_history={"AAA": list(runtime.history["AAA"])})
            self.assertEqual(legacy.splits, {"AAA": []})

    def test_fractional_split_requires_cash_in_lieu_model(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            folder = Path(temp)
            prices = folder / "prices.csv"
            actions = folder / "actions.csv"
            prices.write_text(
                "date,symbol,open,high,low,close,volume\n"
                "2024-01-02,AAA,100,100,100,100,1000\n"
                "2024-01-03,AAA,100,100,100,100,1000\n"
                "2024-01-04,AAA,400,400,400,400,1000\n", encoding="utf-8",
            )
            actions.write_text(
                "date,symbol,kind,value,ex_date\n"
                "2024-01-04,AAA,split,0.25,2024-01-04\n", encoding="utf-8",
            )
            cell = CellSpec("buy_hold", "simple", ("AAA",), 1000,
                            (StrategySpec("strategies:BuyAndHold", 1, {}),))
            spec = RunSpec(str(prices), str(folder / "static.sqlite"), 0, 0,
                           (cell,), str(actions))
            run_id = run_static(spec, "{}", EXAMPLES)
            cell_report = read_report(folder / "static.sqlite", run_id)["cells"][0]
            self.assertEqual(cell_report["status"], "failed")
            self.assertIn("cash-in-lieu", cell_report["error"])
            self.assertEqual(cell_report["final_equity"], 1000)

    def test_dividend_payment_cannot_fund_payment_day_open_order(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            folder = Path(temp)
            prices = folder / "prices.csv"
            actions = folder / "actions.csv"
            prices.write_text(
                "date,symbol,open,high,low,close,volume\n"
                "2024-01-02,AAA,10,10,10,10,1000\n"
                "2024-01-03,AAA,10,10,10,10,1000\n"
                "2024-01-04,AAA,9,9,9,9,1000\n"
                "2024-01-05,AAA,9,9,9,9,1000\n", encoding="utf-8",
            )
            actions.write_text(
                "date,symbol,kind,value,ex_date\n"
                "2024-01-05,AAA,dividend,1,2024-01-04\n", encoding="utf-8",
            )
            cell = CellSpec("buy_hold", "simple", ("AAA",), 1000,
                            (StrategySpec("strategies:BuyAndHold", 1, {}),))
            database = folder / "static.sqlite"
            run_id = run_static(RunSpec(str(prices), str(database), 0, 0,
                                        (cell,), str(actions)), "{}", EXAMPLES)
            with closing(sqlite3.connect(database)) as connection:
                payment_valuation = json.loads(connection.execute(
                    "SELECT payload_json FROM events WHERE run_id=? AND date='2024-01-05' "
                    "AND kind='valuation'", (run_id,),
                ).fetchone()[0])
                payment_orders = connection.execute(
                    "SELECT COUNT(*) FROM events WHERE run_id=? AND date='2024-01-05' "
                    "AND kind='order'", (run_id,),
                ).fetchone()[0]
            self.assertEqual(payment_valuation["positions"], {"AAA": 100})
            self.assertEqual(payment_valuation["cash"], 100)
            self.assertEqual(payment_orders, 0)

    def test_feed_stops_on_action(self) -> None:
        template, raw = load_feed_config(EXAMPLES / "feed_experiment.json")
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            database = Path(temp) / "feed.sqlite"
            spec = FeedSpec("actions_test", date(2024, 1, 2), template.provider,
                            template.market_feed, str(database), 0, 0, template.cells)
            class Provider:
                def actions(self, symbols, start, through):
                    return [{"id": "one", "type": "cash_dividends", "symbol": "AAPL",
                             "process_date": "2024-01-02"}]

                def sessions(self, start, through):
                    return {date(2024, 1, 2)}

                def fetch(self, symbols, start, through):
                    raise AssertionError("Bars should not be fetched after a detected action")

            with self.assertRaisesRegex(ValueError, "Unmodeled corporate action"):
                run_feed_batch(spec, raw, EXAMPLES, Provider(), through=date(2024, 1, 2),
                               now=datetime(2024, 1, 4, tzinfo=timezone.utc))
            self.assertEqual(read_feed_report(database, spec.feed_id)["bars_cached"], 0)

    def test_feed_guard_sees_actions_announced_before_start(self) -> None:
        template, raw = load_feed_config(EXAMPLES / "feed_experiment.json")
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            spec = FeedSpec("lookback_guard", date(2024, 1, 2), template.provider,
                            template.market_feed, str(Path(temp) / "feed.sqlite"),
                            0, 0, template.cells)

            class Provider:
                def actions(self, symbols, start, through):
                    self.assert_start = start
                    return [{"id": "announced-before-start", "type": "forward_splits",
                             "symbol": "AAPL", "process_date": "2023-12-29",
                             "ex_date": "2024-01-02"}]

                def sessions(self, start, through):
                    raise AssertionError("Action guard must stop before calendar and bar requests")

                def fetch(self, symbols, start, through):
                    raise AssertionError("Action guard must stop before bar requests")

            provider = Provider()
            with self.assertRaisesRegex(ValueError, "Unmodeled corporate action"):
                run_feed_batch(spec, raw, EXAMPLES, provider, through=date(2024, 1, 2),
                               now=datetime(2024, 1, 4, tzinfo=timezone.utc))
            self.assertLess(provider.assert_start, spec.start_date)

    def test_feed_guard_ignores_actions_before_experiment(self) -> None:
        template, raw = load_feed_config(EXAMPLES / "feed_experiment.json")
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            spec = FeedSpec("historical_action", date(2024, 1, 2), template.provider,
                            template.market_feed, str(Path(temp) / "feed.sqlite"),
                            0, 0, template.cells)

            class Provider:
                def actions(self, symbols, start, through):
                    return [{"id": "past-split", "type": "forward_splits",
                             "symbol": "AAPL", "process_date": "2023-12-28",
                             "ex_date": "2023-12-29"}]

                def sessions(self, start, through):
                    return {spec.start_date}

                def fetch(self, symbols, start, through):
                    day = spec.start_date
                    return {day: {symbol: Bar(day, symbol, 100, 100, 100, 100, 1000)
                                  for symbol in symbols}}

            result = run_feed_batch(spec, raw, EXAMPLES, Provider(), through=spec.start_date,
                                    now=datetime(2024, 1, 4, tzinfo=timezone.utc))
            self.assertEqual(result["days_processed_this_call"], 1)

    def test_feed_can_explicitly_ignore_actions_in_a_new_experiment(self) -> None:
        template, raw = load_feed_config(EXAMPLES / "feed_experiment.json")
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            spec = FeedSpec("ignored_actions", date(2024, 1, 2), template.provider,
                            template.market_feed, str(Path(temp) / "feed.sqlite"),
                            0, 0, template.cells, True)

            class Provider:
                def actions(self, symbols, start, through):
                    raise AssertionError("Explicit opt-in should skip the action gate")

                def sessions(self, start, through):
                    return {date(2024, 1, 2)}

                def fetch(self, symbols, start, through):
                    day = date(2024, 1, 2)
                    return {day: {symbol: Bar(day, symbol, 100, 100, 100, 100, 1000)
                                  for symbol in symbols}}

            result = run_feed_batch(spec, raw, EXAMPLES, Provider(),
                                    through=date(2024, 1, 2),
                                    now=datetime(2024, 1, 4, tzinfo=timezone.utc))
            self.assertEqual(result["days_processed_this_call"], 1)

    def test_alpaca_action_pages_include_dividends_and_splits(self) -> None:
        calls = []

        def get_json(url, headers):
            calls.append(url)
            if "page_token=" not in url:
                return {"corporate_actions": {"cash_dividends": [
                    {"id": "div", "symbol": "AAPL", "process_date": "2024-01-03",
                     "ex_date": "2024-01-04", "payable_date": "2024-01-08", "rate": "0.25"},
                ]}, "next_page_token": "page2"}
            return {"corporate_actions": {"forward_splits": [
                {"id": "split", "symbol": "AAPL", "process_date": "2024-01-04",
                 "ex_date": "2024-01-05", "old_rate": "1", "new_rate": "2"},
            ]}, "next_page_token": None}

        provider = AlpacaDailyProvider("key", "secret", get_json=get_json)
        actions = provider.actions({"AAPL"}, date(2024, 1, 2), date(2024, 1, 4))
        self.assertEqual({item["type"] for item in actions}, {"cash_dividends", "forward_splits"})
        self.assertEqual(actions[0]["rate"], "0.25")
        self.assertEqual(actions[1]["new_rate"], "2")
        self.assertEqual(len(calls), 2)
        self.assertTrue(all("data.alpaca.markets/v1/corporate-actions" in url for url in calls))
        query = parse_qs(urlparse(calls[0]).query)
        self.assertEqual(query["symbols"], ["AAPL"])
        self.assertEqual(query["data_quality"], ["complete"])
        self.assertNotIn("region", query)


if __name__ == "__main__":
    unittest.main()
