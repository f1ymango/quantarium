"""Behavioral checks for timing, isolation, and allocation."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from quantarium.config import load_config
from quantarium.data import load_daily_csv
from quantarium.engine import run_static
from quantarium.journal import read_report
from quantarium.model import Bar, BarHistory, CellSpec, RunSpec, StrategySpec


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)


class ReplayTests(unittest.TestCase):
    def test_example_runs_with_next_session_orders_and_independent_accounts(self) -> None:
        spec, raw = load_config(EXAMPLES / "experiment.json")
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            spec = RunSpec(
                spec.data_path, str(Path(temp) / "test.sqlite"),
                spec.slippage_bps, spec.commission_per_order, spec.cells,
            )
            run_id = run_static(spec, raw, EXAMPLES)
            report = read_report(Path(spec.database_path), run_id)
            self.assertEqual(report["status"], "completed")
            self.assertEqual(len(report["cells"]), 2)
            connection = sqlite3.connect(spec.database_path)
            try:
                first_order = connection.execute(
                    "SELECT date, payload_json FROM events WHERE run_id=? AND cell_name='single_stock' "
                    "AND kind='order' ORDER BY sequence LIMIT 1", (run_id,),
                ).fetchone()
                self.assertEqual(first_order[0], "2024-01-03")
                order = json.loads(first_order[1])
                self.assertEqual(order["side"], "buy")
                self.assertAlmostEqual(order["fill_price"], 103 * 1.0005)
                self.assertGreater(order["filled_shares"], 0)
                first_signal = connection.execute(
                    "SELECT payload_json FROM events WHERE run_id=? AND cell_name='two_sleeves' "
                    "AND kind='signal' ORDER BY sequence LIMIT 1", (run_id,),
                ).fetchone()
                self.assertEqual(json.loads(first_signal[0])["target_weights"], {"AAA": 0.3, "BBB": 0.3})
                first_valuations = connection.execute(
                    "SELECT cell_name, payload_json FROM events WHERE run_id=? AND kind='valuation' "
                    "AND date='2024-01-02' ORDER BY cell_name", (run_id,),
                ).fetchall()
                self.assertEqual(len(first_valuations), 2)
                self.assertTrue(all(json.loads(row[1])["equity"] == 10000 for row in first_valuations))
                self.assertTrue(all(json.loads(row[1])["positions"] == {
                    s: 0 for s in json.loads(row[1])["positions"]
                } for row in first_valuations))
            finally:
                connection.close()

    def test_two_identical_cells_do_not_share_account_state(self) -> None:
        cells = tuple(CellSpec(
            name, "simple", ("AAA",), 10000,
            (StrategySpec("strategies:BuyAndHold", 1, {}),),
        ) for name in ("one", "two"))
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            spec = RunSpec(str(EXAMPLES / "sample_daily.csv"), str(Path(temp) / "test.sqlite"), 0, 0, cells)
            run_id = run_static(spec, "{}", EXAMPLES)
            report = read_report(Path(spec.database_path), run_id)
            self.assertEqual(report["cells"][0]["final_equity"], report["cells"][1]["final_equity"])
            self.assertGreater(report["cells"][0]["final_equity"], 0)

    def test_missing_bar_rejected_before_a_run_is_created(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            path = Path(temp) / "incomplete.csv"
            path.write_text(
                "date,symbol,open,high,low,close,volume\n"
                "2024-01-02,AAA,10,10,10,10,1\n"
                "2024-01-03,BBB,10,10,10,10,1\n", encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "Missing daily bars"):
                load_daily_csv(path, {"AAA", "BBB"})

    def test_history_view_cannot_see_bars_added_later(self) -> None:
        observed = [Bar(date(2024, 1, 2), "AAA", 10, 10, 10, 10, 1)]
        history = BarHistory(observed)
        observed.append(Bar(date(2024, 1, 3), "AAA", 11, 11, 11, 11, 1))
        self.assertEqual(len(history), 1)
        self.assertEqual(history[-1].date, date(2024, 1, 2))
        self.assertEqual([bar.date for bar in history], [date(2024, 1, 2)])
        with self.assertRaises(IndexError):
            _ = history[1]

    def test_history_view_includes_current_bar_without_exposing_later_bars(self) -> None:
        observed = [Bar(date(2024, 1, 2), "AAA", 10, 10, 10, 10, 1)]
        current = Bar(date(2024, 1, 3), "AAA", 11, 11, 11, 11, 1)
        history = BarHistory(observed, current)
        observed.append(current)
        observed.append(Bar(date(2024, 1, 4), "AAA", 12, 12, 12, 12, 1))
        self.assertEqual([bar.date for bar in history],
                         [date(2024, 1, 2), date(2024, 1, 3)])
        self.assertEqual(history[-1], current)
        self.assertEqual(history[::-1], (current, observed[0]))
        with self.assertRaises(IndexError):
            _ = history[2]


if __name__ == "__main__":
    unittest.main()

