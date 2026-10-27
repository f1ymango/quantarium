"""v0.9.3: the user's own trades as a decision file, replayed and compared with the real fills."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from quantarium.data import load_daily_csv
from quantarium.decisions import load_decisions
from quantarium.engine import run_static
from quantarium.model import CellSpec, RunSpec, StrategySpec
from quantarium.trades import fill_gap, read_trades, trades_to_decisions, write_decisions

ROOT = Path(__file__).resolve().parents[1]
PRICES = ROOT / "examples" / "scenarios" / "steady_bull" / "prices.csv"


class TradesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.days, self.tape, _ = load_daily_csv(PRICES, {"AAA", "BBB"})

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(self, *rows: str) -> Path:
        path = self.folder / "trades.csv"
        path.write_text("date,symbol,side,shares,price,fee\n" + "".join(row + "\n" for row in rows), encoding="utf-8")
        return path

    def test_bad_rows_are_refused_with_their_line(self) -> None:
        for row, message in (("2030-01-08,AAA,buy,1.5,10,0", "fractional"),
                             ("2030-01-08,AAA,short,1,10,0", "side must be"),
                             ("08/01/2030,AAA,buy,1,10,0", "not a date"),
                             ("2030-01-08,AAA,buy,1,-3,0", "price must be positive"),
                             ("2030-01-08,AA A,buy,1,10,0", "not a symbol")):
            with self.subTest(row=row), self.assertRaisesRegex(ValueError, f"Line 2: .*{message}"):
                read_trades(self.write(row))

    def test_trades_become_market_orders_decided_the_close_before(self) -> None:
        d = self.days
        trades = read_trades(self.write(
            f"{d[3]},AAA,buy,50,{self.tape[d[3]]['AAA'].open},1",
            f"{d[3]},AAA,buy,30,{self.tape[d[3]]['AAA'].open},1",
            f"{d[5]},BBB,buy,10,80,0", f"{d[5]},BBB,sell,10,81,0",
            f"{d[8]},BBB,sell,5,82,0",
            f"{d[10]},AAA,sell,20,{self.tape[d[10]]['AAA'].open},1"))
        document, report = trades_to_decisions(trades, prices=PRICES)
        dated = {item["date"]: item for item in document["decisions"]}
        first = dated[d[2].isoformat()]
        self.assertIsNone(first["weights"])
        self.assertEqual(first["orders"], [{"symbol": "AAA", "side": "buy", "kind": "market", "shares": 80,
                                            "price": round(self.tape[d[3]]["AAA"].open, 6),
                                            "tag": f"real {self.tape[d[3]]['AAA'].open:.4f}"}])
        self.assertNotIn(d[4].isoformat(), dated)  # the round trip nets to nothing
        self.assertEqual(report["initial_positions"], {"BBB": {"shares": 5}})
        self.assertTrue(any("round trip" in warning for warning in report["warnings"]))
        self.assertTrue(any("Deposits and withdrawals" in limit for limit in report["limits"]))

    def test_a_trade_off_the_calendar_or_on_its_first_day_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "not a trading session"):
            trades_to_decisions(read_trades(self.write("2030-01-05,AAA,buy,1,10,0")), prices=PRICES)
        with self.assertRaisesRegex(ValueError, "first session"):
            trades_to_decisions(read_trades(self.write(f"{self.days[0]},AAA,buy,1,10,0")), prices=PRICES)
        # Without a price file, the NYSE calendar: 2024-07-04 is a holiday.
        with self.assertRaisesRegex(ValueError, "NYSE calendar"):
            trades_to_decisions(read_trades(self.write("2024-07-04,AAA,buy,1,10,0")))
        with self.assertRaisesRegex(ValueError, "at most 60 years"):
            trades_to_decisions(read_trades(self.write("2024-07-05,AAA,buy,1,10,0", "9999-12-31,AAA,sell,1,10,0")))

    def test_the_replay_fills_at_the_open_and_the_gap_is_measured(self) -> None:
        d = self.days
        buy_real = self.tape[d[3]]["AAA"].open * 1.01   # the real buy paid 1% above the open
        sell_real = self.tape[d[10]]["AAA"].open        # the real sell got exactly the open
        trades = self.write(f"{d[3]},AAA,buy,50,{buy_real},0", f"{d[10]},AAA,sell,20,{sell_real},0")
        output = self.folder / "decisions" / "mine.json"
        report = write_decisions(trades, output, prices=PRICES)
        self.assertEqual((report["decisions"], report["orders"]), (2, 2))
        sha = load_decisions(output).sha256
        cell = CellSpec("mine", "complex", ("AAA", "BBB"), 50_000, (StrategySpec(
            "quantarium.referee:ReplayDecisions", 1, {"decisions": str(output), "sha256": sha}),))
        database = self.folder / "replay.sqlite"
        run_static(RunSpec(str(PRICES), str(database), 0, 0, (cell,)), "{}", self.folder)
        gap = fill_gap(trades, database, "mine")
        self.assertEqual((gap["compared"], gap["unfilled"]), (2, []))
        buy, sell = gap["rows"]
        self.assertAlmostEqual(buy["gap_bps"], (1 / 1.01 - 1) * 10_000, places=6)  # Quantarium paid less
        self.assertAlmostEqual(sell["gap_bps"], 0, places=6)
        self.assertLess(gap["total_gap_usd"], 0)
        with self.assertRaises(FileExistsError):
            write_decisions(trades, output, prices=PRICES)


if __name__ == "__main__":
    unittest.main()
