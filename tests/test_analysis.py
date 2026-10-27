"""Performance analysis math and journal integration."""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from quantarium.analysis import _cell_curve, read_analysis, write_curve_csv
from quantarium.config import load_config, load_feed_config
from quantarium.engine import run_static
from quantarium.feed import run_feed_batch
from quantarium.journal import read_report
from quantarium.model import Bar, FeedSpec, RunSpec


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)


class TwoDayProvider:
    def __init__(self) -> None:
        self.tape = {
            date(2024, 1, 2): {
                "AAPL": Bar(date(2024, 1, 2), "AAPL", 10, 11, 9, 10, 1000),
                "MSFT": Bar(date(2024, 1, 2), "MSFT", 20, 21, 19, 20, 1000),
            },
            date(2024, 1, 3): {
                "AAPL": Bar(date(2024, 1, 3), "AAPL", 11, 12, 10, 11, 1000),
                "MSFT": Bar(date(2024, 1, 3), "MSFT", 21, 22, 20, 21, 1000),
            },
        }

    def sessions(self, start: date, through: date) -> set[date]:
        return {day for day in self.tape if start <= day <= through}

    def actions(self, symbols: set[str], start: date, through: date) -> list[dict]:
        return []

    def fetch(self, symbols: set[str], start: date, through: date) -> dict[date, dict[str, Bar]]:
        return {
            day: {symbol: bars[symbol] for symbol in symbols}
            for day, bars in self.tape.items() if start <= day <= through
        }


class AnalysisTests(unittest.TestCase):
    def test_drawdown_includes_initial_cash_and_orders_use_filled_notional(self) -> None:
        valuations = [("2024-01-02", json.dumps({"equity": 90})),
                      ("2024-01-03", json.dumps({"equity": 120})),
                      ("2024-01-04", json.dumps({"equity": 60}))]
        orders = [json.dumps({"filled_shares": 2, "fill_price": 10, "commission": 1}),
                  json.dumps({"filled_shares": 0, "fill_price": None, "commission": 0})]
        summary, curve = _cell_curve(100, valuations, orders)
        self.assertEqual(summary["total_return_pct"], -40)
        self.assertEqual(summary["max_drawdown_pct"], 50)
        self.assertEqual(summary["max_drawdown_date"], "2024-01-04")
        self.assertEqual(summary["orders_submitted"], 2)
        self.assertEqual(summary["orders_filled"], 1)
        self.assertEqual(summary["traded_notional"], 20)
        self.assertEqual(summary["commission_total"], 1)
        self.assertAlmostEqual(curve[0]["drawdown_pct"], 10)
        self.assertAlmostEqual(curve[0]["daily_return_pct"], -10)

    def test_static_cells_compare_with_saved_report_and_export_csv(self) -> None:
        spec, raw = load_config(EXAMPLES / "experiment.json")
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            database = Path(temp) / "static.sqlite"
            spec = RunSpec(spec.data_path, str(database), spec.slippage_bps,
                           spec.commission_per_order, spec.cells)
            run_id = run_static(spec, raw, EXAMPLES)
            result = read_analysis(database, "static", run_id, benchmark="single_stock")
            report = read_report(database, run_id)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(len(result["cells"]), 2)
            for cell in result["cells"]:
                saved = next(item for item in report["cells"] if item["name"] == cell["name"])
                self.assertAlmostEqual(cell["total_return_pct"], saved["return_pct"])
            self.assertEqual(next(cell for cell in result["cells"]
                                  if cell["name"] == "single_stock")["excess_return_pp"], 0)
            exported = Path(temp) / "curve.csv"
            write_curve_csv(exported, result)
            with exported.open(encoding="utf-8", newline="") as source:
                rows = list(csv.DictReader(source))
            self.assertEqual(len(rows), len(result["curve"]))
            self.assertEqual(rows[0]["date"], min(point["date"] for point in result["curve"]))
            self.assertIn("benchmark_index_100", rows[0])
            with self.assertRaisesRegex(ValueError, "Benchmark cell not found"):
                read_analysis(database, "static", run_id, benchmark="absent")

    def test_feed_analysis_uses_incremental_checkpoints_and_handles_empty_feed(self) -> None:
        template, raw = load_feed_config(EXAMPLES / "feed_experiment.json")
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            database = Path(temp) / "feed.sqlite"
            spec = FeedSpec("analysis_test", date(2024, 1, 2), template.provider,
                            template.market_feed, str(database), template.slippage_bps,
                            template.commission_per_order, template.cells)
            provider = TwoDayProvider()
            now = datetime(2024, 1, 5, 12, tzinfo=timezone.utc)
            run_feed_batch(spec, raw, EXAMPLES, provider, through=date(2024, 1, 2), now=now)
            first = read_analysis(database, "feed", spec.feed_id, benchmark="single_stock")
            self.assertTrue(all(cell["sessions"] == 1 for cell in first["cells"]))
            run_feed_batch(spec, raw, EXAMPLES, provider, through=date(2024, 1, 3), now=now)
            second = read_analysis(database, "feed", spec.feed_id, benchmark="single_stock")
            self.assertTrue(all(cell["sessions"] == 2 for cell in second["cells"]))
            self.assertEqual(len(second["curve"]), 4)
            self.assertEqual(next(cell for cell in second["cells"]
                                  if cell["name"] == "single_stock")["excess_return_pp"], 0)
            empty = FeedSpec("empty_analysis", date(2024, 1, 4), template.provider,
                             template.market_feed, str(Path(temp) / "empty.sqlite"),
                             template.slippage_bps, template.commission_per_order, template.cells)
            run_feed_batch(empty, raw, EXAMPLES, provider, through=date(2024, 1, 3), now=now)
            waiting = read_analysis(Path(empty.database_path), "feed", empty.feed_id)
            self.assertEqual(waiting["status"], "waiting_for_data")
            self.assertEqual(waiting["curve"], [])
            self.assertTrue(all(cell["total_return_pct"] is None for cell in waiting["cells"]))


if __name__ == "__main__":
    unittest.main()


class RiskMetricTests(unittest.TestCase):
    def test_volatility_sharpe_turnover_and_exposure(self) -> None:
        # Equity 100 -> 110 -> 99 -> 108.9: returns +10%, -10%, +10%.
        valuations = [
            ("2024-01-02", json.dumps({"equity": 110, "cash": 110})),
            ("2024-01-03", json.dumps({"equity": 99, "cash": 49.5})),
            ("2024-01-04", json.dumps({"equity": 108.9, "cash": 0, "receivables": {"x": 10.89}})),
        ]
        orders = [json.dumps({"filled_shares": 2, "fill_price": 50, "commission": 1})]
        summary, _ = _cell_curve(100, valuations, orders)
        mean = 0.1 / 3
        deviation = (((0.1 - mean) ** 2 * 2 + (-0.1 - mean) ** 2) / 2) ** 0.5
        self.assertAlmostEqual(summary["annualized_volatility_pct"], 100 * deviation * 252 ** 0.5)
        self.assertAlmostEqual(summary["sharpe_ratio_rf0"], mean / deviation * 252 ** 0.5)
        self.assertAlmostEqual(summary["turnover_ratio"], 100 / ((110 + 99 + 108.9) / 3))
        self.assertAlmostEqual(summary["average_exposure_pct"], (0 + 50 + 90) / 3)

    def test_metrics_need_two_returns(self) -> None:
        summary, _ = _cell_curve(100, [("2024-01-02", json.dumps({"equity": 100, "cash": 100}))], [])
        self.assertIsNone(summary["annualized_volatility_pct"])
        self.assertIsNone(summary["sharpe_ratio_rf0"])
        self.assertEqual(summary["turnover_ratio"], 0)
