"""Saved cell timelines expose decisions and account events without replay."""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from quantarium.config import load_config
from quantarium.engine import run_static
from quantarium.feed import run_feed_batch
from quantarium.model import Bar, CellSpec, FeedSpec, RunSpec, StrategySpec
from quantarium.timeline import read_timeline, write_timeline_csv


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)


class TimelineTests(unittest.TestCase):
    def test_static_timeline_filters_and_exports_decisions(self) -> None:
        template, raw = load_config(EXAMPLES / "experiment.json")
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            database = Path(temp) / "static.sqlite"
            spec = RunSpec(template.data_path, str(database), template.slippage_bps,
                           template.commission_per_order, template.cells)
            run_id = run_static(spec, raw, EXAMPLES)
            timeline = read_timeline(database, "static", "single_stock", run_id)
            self.assertEqual(timeline["status"], "completed")
            self.assertEqual(timeline["days"][0]["date"], "2024-01-02")
            self.assertEqual(timeline["days"][0]["signal"]["target_weights"],
                             {"AAA": 1.0})
            self.assertEqual(timeline["days"][1]["orders"][0]["side"], "buy")
            self.assertEqual(len(timeline["days"][0]["strategies"]), 1)
            filtered = read_timeline(database, "static", "single_stock", run_id,
                                     start=date(2024, 1, 3), through=date(2024, 1, 3))
            self.assertEqual([day["date"] for day in filtered["days"]], ["2024-01-03"])
            csv_path = Path(temp) / "timeline.csv"
            write_timeline_csv(csv_path, filtered)
            with csv_path.open(newline="", encoding="utf-8") as source:
                rows = list(csv.DictReader(source))
            self.assertEqual(len(rows), 1)
            self.assertEqual(json.loads(rows[0]["orders_json"])[0]["side"], "buy")
            with self.assertRaisesRegex(ValueError, "Cell not found"):
                read_timeline(database, "static", "missing", run_id)

    def test_feed_timeline_reads_active_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            database = Path(temp) / "feed.sqlite"
            cell = CellSpec("single", "simple", ("AAA",), 1000,
                            (StrategySpec("strategies:BuyAndHold", 1, {}),))
            spec = FeedSpec("timeline_test", date(2024, 1, 2), "alpaca", "iex",
                            str(database), 0, 0, (cell,))

            class Provider:
                def actions(self, symbols, start, through):
                    return []

                def sessions(self, start, through):
                    return {date(2024, 1, 2), date(2024, 1, 3)}

                def fetch(self, symbols, start, through):
                    return {day: {"AAA": Bar(day, "AAA", 10, 10, 10, 10, 1000)}
                            for day in self.sessions(start, through)}

            run_feed_batch(spec, "{}", EXAMPLES, Provider(),
                           through=date(2024, 1, 3),
                           now=datetime(2024, 1, 6, tzinfo=timezone.utc))
            timeline = read_timeline(database, "feed", "single", spec.feed_id)
            self.assertEqual(timeline["status"], "active")
            self.assertEqual([day["date"] for day in timeline["days"]],
                             ["2024-01-02", "2024-01-03"])
            self.assertEqual(timeline["days"][1]["orders"][0]["filled_shares"], 100)
            with self.assertRaisesRegex(ValueError, "requires a feed_id"):
                read_timeline(database, "feed", "single")

    def test_static_corporate_actions_appear_on_relevant_dates(self) -> None:
        template, raw = load_config(EXAMPLES / "action_experiment.json")
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            database = Path(temp) / "actions.sqlite"
            spec = RunSpec(template.data_path, str(database), 0, 0,
                           template.cells, template.actions_path)
            run_id = run_static(spec, raw, EXAMPLES)
            timeline = read_timeline(database, "static", "action_buy_hold", run_id)
            by_date = {day["date"]: day for day in timeline["days"]}
            self.assertEqual(by_date["2024-01-04"]["corporate_actions"][0]["kind"], "split")
            self.assertEqual(by_date["2024-01-05"]["corporate_actions"][0]["kind"],
                             "dividend_entitlement")
            self.assertEqual(by_date["2024-01-08"]["corporate_actions"][0]["kind"],
                             "dividend_payment")


if __name__ == "__main__":
    unittest.main()
