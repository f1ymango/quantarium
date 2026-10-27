"""Feed data export is reproducible, complete, and usable by Static replay."""

from __future__ import annotations

import hashlib
import csv
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path

from quantarium.data import load_daily_csv
from quantarium.audit import audit_replay
from quantarium.engine import run_static
from quantarium.export import export_feed_csv
from quantarium.feed import run_feed_batch
from quantarium.journal import read_report
from quantarium.model import Bar, CellSpec, FeedSpec, RunSpec, StrategySpec


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)
FIRST = date(2024, 1, 2)
SECOND = date(2024, 1, 3)
THIRD = date(2024, 1, 4)
NOW = datetime(2024, 1, 6, tzinfo=timezone.utc)


def _bar(day: date, symbol: str, price: float) -> Bar:
    return Bar(day, symbol, price, price, price, price, 1000)


class Provider:
    def __init__(self) -> None:
        self.tape = {
            FIRST: {"AAA": _bar(FIRST, "AAA", 10), "BBB": _bar(FIRST, "BBB", 20)},
            SECOND: {"AAA": _bar(SECOND, "AAA", 11), "BBB": _bar(SECOND, "BBB", 21)},
        }

    def actions(self, symbols, start, through):
        return []

    def sessions(self, start, through):
        return {day for day in self.tape if start <= day <= through}

    def fetch(self, symbols, start, through):
        return {day: {symbol: bar for symbol, bar in bars.items() if symbol in symbols}
                for day, bars in self.tape.items() if start <= day <= through}


class DividendProvider(Provider):
    def __init__(self) -> None:
        super().__init__()
        self.tape = {
            FIRST: {"AAA": _bar(FIRST, "AAA", 10)},
            SECOND: {"AAA": _bar(SECOND, "AAA", 10)},
            THIRD: {"AAA": _bar(THIRD, "AAA", 9)},
        }

    def actions(self, symbols, start, through):
        records = [
            {"id": source_id, "type": "cash_dividends", "symbol": "AAA",
             "process_date": "2024-01-02", "ex_date": "2024-01-04",
             "payable_date": "2024-01-05", "rate": "0.5"}
            for source_id in ("div-one", "div-two")
        ]
        return records if start <= FIRST <= through else []


class ExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.folder = Path(self.temp.name)
        cell = CellSpec("basket", "complex", ("AAA", "BBB"), 1000,
                        (StrategySpec("strategies:EqualWeight", 1, {}),))
        self.database = self.folder / "feed.sqlite"
        self.spec = FeedSpec("export_test", FIRST, "alpaca", "iex",
                             str(self.database), 0, 0, (cell,))
        self.provider = Provider()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _feed(self) -> None:
        run_feed_batch(self.spec, "{}", EXAMPLES, self.provider,
                       through=SECOND, now=NOW)

    def test_export_uses_first_seen_bars_and_replays_statically(self) -> None:
        self._feed()
        self.provider.tape[SECOND]["AAA"] = _bar(SECOND, "AAA", 99)
        self._feed()
        output = self.folder / "captured.csv"
        result = export_feed_csv(self.database, self.spec.feed_id, output)
        self.assertEqual(result["bars"], 4)
        self.assertEqual(result["symbols"], ["AAA", "BBB"])
        self.assertEqual(result["last_date"], str(SECOND))
        self.assertEqual(result["csv_sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
        self.assertEqual(json.loads((self.folder / "captured.csv.manifest.json").read_text(
            encoding="utf-8"))["csv_sha256"], result["csv_sha256"])
        days, tape, _ = load_daily_csv(output, {"AAA", "BBB"})
        self.assertEqual(days, [FIRST, SECOND])
        self.assertEqual(tape[SECOND]["AAA"].close, 11)
        static_cell = CellSpec("buy_hold", "simple", ("AAA",), 1000,
                               (StrategySpec("strategies:BuyAndHold", 1, {}),))
        static_db = self.folder / "static.sqlite"
        run_id = run_static(RunSpec(str(output), str(static_db), 0, 0, (static_cell,)),
                            "{}", EXAMPLES)
        self.assertEqual(read_report(static_db, run_id)["status"], "completed")
        basket_id = run_static(RunSpec(str(output), str(static_db), 0, 0,
                                       self.spec.cells), "{}", EXAMPLES)
        self.assertTrue(audit_replay(self.database, self.spec.feed_id,
                                     static_db, basket_id)["matched"])
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM feed_revisions WHERE feed_id='export_test'"
            ).fetchone()[0], 1)

    def test_through_and_overwrite_are_explicit(self) -> None:
        self._feed()
        output = self.folder / "one_day.csv"
        one = export_feed_csv(self.database, self.spec.feed_id, output, FIRST)
        self.assertEqual((one["bars"], one["last_date"]), (2, str(FIRST)))
        with self.assertRaises(FileExistsError):
            export_feed_csv(self.database, self.spec.feed_id, output)
        all_days = export_feed_csv(self.database, self.spec.feed_id, output,
                                   overwrite=True)
        self.assertEqual(all_days["bars"], 4)
        self.assertEqual(len(output.read_text(encoding="utf-8").splitlines()), 5)

    def test_missing_experiment_and_empty_feed_fail_without_output(self) -> None:
        self._feed()
        output = self.folder / "missing.csv"
        with self.assertRaisesRegex(ValueError, "not found"):
            export_feed_csv(self.database, "unknown", output)
        self.assertFalse(output.exists())
        with self.assertRaisesRegex(ValueError, "no saved bars"):
            export_feed_csv(self.database, self.spec.feed_id, output,
                            through=date(2024, 1, 1))
        self.assertFalse(output.exists())

    def test_export_replays_multiple_unpaid_dividends(self) -> None:
        cell = CellSpec("buy_hold", "simple", ("AAA",), 1000,
                        (StrategySpec("strategies:BuyAndHold", 1, {}),))
        spec = FeedSpec("dividend_export", FIRST, "alpaca", "iex", str(self.database),
                        0, 0, (cell,), False, True)
        run_feed_batch(spec, "{}", EXAMPLES, DividendProvider(), through=THIRD, now=NOW)
        output = self.folder / "dividend.csv"
        result = export_feed_csv(self.database, spec.feed_id, output)
        actions_file = self.folder / "dividend.actions.csv"
        with actions_file.open(newline="", encoding="utf-8") as source:
            actions = list(csv.DictReader(source))
        self.assertEqual(len(actions), 2)
        self.assertEqual({row["source_id"] for row in actions}, {"div-one", "div-two"})
        self.assertEqual(result["actions"], 2)
        self.assertEqual(result["actions_sha256"], hashlib.sha256(actions_file.read_bytes()).hexdigest())
        static_db = self.folder / "dividend_static.sqlite"
        static_spec = RunSpec(str(output), str(static_db), 0, 0, (cell,), str(actions_file))
        run_id = run_static(static_spec, "{}", EXAMPLES)
        self.assertEqual(read_report(static_db, run_id)["cells"][0]["final_equity"], 1000)
        with closing(sqlite3.connect(static_db)) as connection:
            valuation = json.loads(connection.execute(
                "SELECT payload_json FROM events WHERE run_id=? AND kind='valuation' "
                "ORDER BY date DESC LIMIT 1", (run_id,),
            ).fetchone()[0])
        self.assertEqual(valuation["receivables"], {"div-one": 50, "div-two": 50})
        matching = audit_replay(self.database, spec.feed_id, static_db, run_id)
        self.assertTrue(matching["matched"], matching["mismatches"])
        self.assertGreater(matching["events_compared"], 0)

        no_actions_db = self.folder / "no_actions.sqlite"
        no_actions_id = run_static(RunSpec(str(output), str(no_actions_db), 0, 0,
                                           (cell,)), "{}", EXAMPLES)
        divergent = audit_replay(self.database, spec.feed_id, no_actions_db, no_actions_id)
        self.assertFalse(divergent["matched"])
        self.assertTrue(divergent["mismatches"])


if __name__ == "__main__":
    unittest.main()
