"""Incremental Feed accounting for supported corporate actions."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path

from quantarium.actions import normalize_feed_actions
from quantarium.config import load_feed_config
from quantarium.feed import read_feed_report, run_feed_batch
from quantarium.model import Bar, CellSpec, FeedSpec, StrategySpec
from quantarium.provider import AlpacaDailyProvider


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)
DAYS = [date(2024, 1, day) for day in (2, 3, 4, 5, 8)]
NOW = datetime(2024, 1, 10, 12, tzinfo=timezone.utc)


def split(ratio: str = "2") -> dict:
    return {"id": "split-1", "type": "forward_splits", "symbol": "AAA",
            "process_date": "2023-12-29", "ex_date": "2024-01-04",
            "new_rate": ratio, "old_rate": "1"}


def dividend() -> dict:
    return {"id": "div-1", "type": "cash_dividends", "symbol": "AAA",
            "process_date": "2024-01-03", "ex_date": "2024-01-05",
            "payable_date": "2024-01-08", "rate": "1", "foreign": False}


class Provider:
    def __init__(self, records: list[dict]):
        self.records = records
        self.prices = {DAYS[0]: 100, DAYS[1]: 100, DAYS[2]: 50,
                       DAYS[3]: 49, DAYS[4]: 49}

    def actions(self, symbols, start, through):
        return [record.copy() for record in self.records
                if start <= date.fromisoformat(record["process_date"]) <= through]

    def sessions(self, start, through):
        return {day for day in DAYS if start <= day <= through}

    def fetch(self, symbols, start, through):
        return {day: {"AAA": Bar(day, "AAA", price, price, price, price, 1000)}
                for day, price in self.prices.items() if start <= day <= through}


class FeedActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.database = Path(self.temp.name) / "feed.sqlite"
        cell = CellSpec("buy_hold", "simple", ("AAA",), 1000,
                        (StrategySpec("strategies:BuyAndHold", 1, {}),))
        self.spec = FeedSpec("actions_feed", DAYS[0], "alpaca", "iex",
                             str(self.database), 0, 0, (cell,), False, True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_batch(self, provider: Provider, through: date) -> dict:
        return run_feed_batch(self.spec, "{}", EXAMPLES, provider,
                              through=through, now=NOW)

    def test_split_and_dividend_survive_daily_restarts(self) -> None:
        provider = Provider([split(), dividend()])
        for day in DAYS:
            self.assertEqual(self.run_batch(provider, day)["days_processed_this_call"], 1)
        report = read_feed_report(self.database, self.spec.feed_id)
        self.assertEqual(report["actions_pinned"], 2)
        self.assertEqual(report["cells"][0]["equity"], 1000)
        self.assertEqual(report["cells"][0]["positions"], {"AAA": 20})
        with closing(sqlite3.connect(self.database)) as connection:
            actions = [(day, json.loads(payload)) for day, payload in connection.execute(
                "SELECT date, payload_json FROM feed_events WHERE feed_id=? "
                "AND kind='corporate_action' ORDER BY sequence", (self.spec.feed_id,),
            )]
            valuations = {day: json.loads(payload) for day, payload in connection.execute(
                "SELECT date, payload_json FROM feed_events WHERE feed_id=? "
                "AND kind='valuation'", (self.spec.feed_id,),
            )}
        self.assertEqual([item[1]["kind"] for item in actions],
                         ["split", "dividend_entitlement", "dividend_payment"])
        self.assertEqual(actions[0][1]["shares_after"], 20)
        self.assertEqual(actions[1][1]["receivable"], 20)
        self.assertEqual(actions[2][1]["cash_paid"], 20)
        self.assertEqual(valuations["2024-01-05"]["equity"], 1000)
        self.assertEqual(valuations["2024-01-05"]["receivables"], {"div-1": 20})

    def test_late_action_stops_before_next_session(self) -> None:
        provider = Provider([])
        self.run_batch(provider, DAYS[2])
        provider.records = [split()]
        with self.assertRaisesRegex(ValueError, "Late corporate action"):
            self.run_batch(provider, DAYS[3])
        self.assertEqual(read_feed_report(self.database, self.spec.feed_id)["bars_cached"], 3)

    def test_revised_terms_stop_before_next_session(self) -> None:
        provider = Provider([split()])
        self.run_batch(provider, DAYS[2])
        provider.records = [split("3")]
        with self.assertRaisesRegex(ValueError, "corporate action changed"):
            self.run_batch(provider, DAYS[3])
        self.assertEqual(read_feed_report(self.database, self.spec.feed_id)["bars_cached"], 3)

    def test_disappeared_pinned_action_stops(self) -> None:
        provider = Provider([split()])
        self.run_batch(provider, DAYS[2])
        provider.records = []
        with self.assertRaisesRegex(ValueError, "corporate action disappeared"):
            self.run_batch(provider, DAYS[3])

    def test_fractional_reverse_split_fails_only_the_cell(self) -> None:
        reverse = {**split("1"), "type": "reverse_splits", "old_rate": "3"}
        provider = Provider([reverse])
        report = self.run_batch(provider, DAYS[2])
        self.assertEqual(report["actions_pinned"], 1)
        cell = report["cells"][0]
        self.assertEqual(cell["status"], "failed")
        self.assertIn("cash-in-lieu", cell["error"])
        self.assertEqual(cell["last_date"], DAYS[1].isoformat())
        later = self.run_batch(provider, DAYS[3])
        self.assertEqual(later["days_processed_this_call"], 0)
        self.assertEqual(later["active_cells"], 0)

    def test_unsupported_and_incomplete_actions_fail_closed(self) -> None:
        unsupported = {"id": "spinoff-1", "type": "spin_offs", "symbol": "AAA",
                       "process_date": "2024-01-03", "ex_date": "2024-01-04"}
        with self.assertRaisesRegex(ValueError, "Unsupported corporate action"):
            self.run_batch(Provider([unsupported]), DAYS[2])
        missing_payment = dividend()
        del missing_payment["payable_date"]
        with self.assertRaisesRegex(ValueError, "Incomplete corporate action"):
            normalize_feed_actions([missing_payment], DAYS[0], DAYS[-1])
        self.assertEqual(read_feed_report(self.database, self.spec.feed_id)["bars_cached"], 0)

    def test_legacy_feed_report_without_action_table(self) -> None:
        self.run_batch(Provider([]), DAYS[0])
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("DROP TABLE feed_actions")
            connection.commit()
        self.assertEqual(read_feed_report(self.database, self.spec.feed_id)["actions_pinned"], 0)

    def test_provider_identifies_old_symbol_for_unsupported_action(self) -> None:
        provider = AlpacaDailyProvider("key", "secret", get_json=lambda url, headers: {
            "corporate_actions": {"name_changes": [{
                "id": "rename", "old_symbol": "AAA", "new_symbol": "AAB",
                "process_date": "2024-01-03",
            }]}, "next_page_token": None,
        })
        actions = provider.actions({"AAA"}, DAYS[0], DAYS[2])
        self.assertEqual(actions[0]["symbol"], "AAA")
        with self.assertRaisesRegex(ValueError, "Unsupported corporate action"):
            normalize_feed_actions(actions, DAYS[0], DAYS[2])

    def test_config_requires_one_action_mode(self) -> None:
        raw = json.loads((EXAMPLES / "feed_experiment.json").read_text(encoding="utf-8"))
        raw["apply_supported_corporate_actions"] = True
        raw["ignore_corporate_actions"] = True
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            config = Path(temp) / "feed.json"
            config.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "cannot apply and ignore"):
                load_feed_config(config)
        conflicting = FeedSpec(self.spec.feed_id, self.spec.start_date, self.spec.provider,
                               self.spec.market_feed, self.spec.database_path, 0, 0,
                               self.spec.cells, True, True)
        with self.assertRaisesRegex(ValueError, "cannot apply and ignore"):
            run_feed_batch(conflicting, "{}", EXAMPLES, Provider([]), through=DAYS[0], now=NOW)

    def test_provider_rejects_missing_action_id(self) -> None:
        provider = AlpacaDailyProvider("key", "secret", get_json=lambda url, headers: {
            "corporate_actions": {"cash_dividends": [{
                "symbol": "AAA", "process_date": "2024-01-03",
            }]}, "next_page_token": None,
        })
        with self.assertRaisesRegex(ValueError, "without ID"):
            provider.actions({"AAA"}, DAYS[0], DAYS[2])


if __name__ == "__main__":
    unittest.main()
