"""Keyless real-data path: vendor CSV import, offline calendar, csv Feed."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from quantarium.config import load_feed_config
from quantarium.data import load_daily_csv
from quantarium.feed import run_feed_batch
from quantarium.importer import CsvFeedProvider, import_bars, read_vendor_csv
from quantarium.market_calendar import holidays, sessions


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)

YAHOO = """Date,Open,High,Low,Close,Adj Close,Volume
2024-01-02,10,11,9,10,5,100
2024-01-03,null,null,null,null,null,null
2024-01-04,10,12,10,11,5.5,200
"""
NASDAQ = """Date,Close/Last,Volume,Open,High,Low
01/04/2024,$21.00,"1,500",$20.00,$22.00,$19.50
01/02/2024,$20.00,1000,$20.00,$20.50,$19.00
"""


class CalendarTests(unittest.TestCase):
    def test_session_counts_match_published_nyse_years(self) -> None:
        published = {2019: 252, 2020: 253, 2021: 252, 2022: 251, 2023: 250, 2024: 252, 2025: 250}
        for year, count in published.items():
            self.assertEqual(len(sessions(date(year, 1, 1), date(year, 12, 31))), count, year)

    def test_observance_rules(self) -> None:
        self.assertNotIn(date(2021, 12, 31), holidays(2022))  # Saturday New Year not moved back
        self.assertIn(date(2022, 6, 20), holidays(2022))      # Sunday Juneteenth observed Monday
        self.assertNotIn(date(2021, 6, 18), holidays(2021))   # Juneteenth began in 2022
        self.assertIn(date(2026, 7, 3), holidays(2026))       # Saturday July 4 observed Friday
        self.assertIn(date(2008, 3, 21), holidays(2008))      # Good Friday
        self.assertNotIn(date(2012, 10, 29), sessions(date(2012, 10, 29), date(2012, 10, 29)))


class ImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.folder = Path(self.temp.name)
        (self.folder / "aaa.csv").write_text(YAHOO, encoding="utf-8")
        (self.folder / "bbb.csv").write_text(NASDAQ, encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_layouts_are_detected_and_placeholders_skipped(self) -> None:
        bars, layout = read_vendor_csv(self.folder / "aaa.csv", "AAA")
        self.assertEqual(layout, "yahoo")
        self.assertEqual([bar.date for bar in bars], [date(2024, 1, 2), date(2024, 1, 4)])
        adjusted, _ = read_vendor_csv(self.folder / "aaa.csv", "AAA", price_basis="adjusted")
        self.assertEqual((adjusted[0].open, adjusted[0].close), (5, 5))
        nasdaq, layout = read_vendor_csv(self.folder / "bbb.csv", "BBB")
        self.assertEqual(layout, "nasdaq")
        self.assertEqual((nasdaq[1].date, nasdaq[1].close, nasdaq[1].volume),
                         (date(2024, 1, 4), 21.0, 1500))
        with self.assertRaisesRegex(ValueError, "Adj Close"):
            read_vendor_csv(self.folder / "bbb.csv", "BBB", price_basis="adjusted")

    def test_merge_writes_a_loadable_tape_with_provenance(self) -> None:
        output = self.folder / "tape.csv"
        result = import_bars({"aaa": self.folder / "aaa.csv", "BBB": self.folder / "bbb.csv"},
                             output)
        self.assertEqual(result["symbols"], ["AAA", "BBB"])
        days, tape, _ = load_daily_csv(output, {"AAA", "BBB"})
        self.assertEqual(days, [date(2024, 1, 2), date(2024, 1, 4)])
        manifest = json.loads((self.folder / "tape.csv.manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["sources"]["BBB"]["layout"], "nasdaq")
        with self.assertRaises(FileExistsError):
            import_bars({"AAA": self.folder / "aaa.csv"}, output)

    def test_mismatched_dates_fail_unless_common_dates(self) -> None:
        (self.folder / "ccc.csv").write_text(
            "Date,Open,High,Low,Close,Volume\n2024-01-02,1,1,1,1,1\n", encoding="utf-8")
        sources = {"AAA": self.folder / "aaa.csv", "CCC": self.folder / "ccc.csv"}
        with self.assertRaisesRegex(ValueError, "2024-01-04 \\(missing CCC\\)"):
            import_bars(sources, self.folder / "x.csv")
        result = import_bars(sources, self.folder / "x.csv", common_dates=True)
        self.assertEqual((result["sessions"], result["dropped_dates"]), (1, 1))


class CsvFeedTests(unittest.TestCase):
    def test_appended_days_advance_and_a_skipped_download_stops(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            folder = Path(temp)
            tape = folder / "tape.csv"

            def write(days: list[int]) -> None:
                rows = ["date,symbol,open,high,low,close,volume"]
                rows += [f"2024-01-{day:02d},AAPL,{day},{day},{day},{day},100" for day in days]
                tape.write_text("\n".join(rows) + "\n", encoding="utf-8")

            config = json.loads((EXAMPLES / "feed_experiment.json").read_text(encoding="utf-8"))
            config.update({"provider": "csv", "data_path": str(tape), "start_date": "2024-01-02",
                           "database_path": str(folder / "feed.sqlite")})
            config["cells"] = [config["cells"][0]]
            path = folder / "feed.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            spec, raw = load_feed_config(path)
            self.assertEqual(spec.market_feed, "file")
            now = datetime(2024, 1, 20, tzinfo=timezone.utc)

            write([2, 3, 4])
            first = run_feed_batch(spec, raw, EXAMPLES, CsvFeedProvider(tape), now=now)
            self.assertEqual(first["days_processed_this_call"], 3)
            write([2, 3, 4, 5])
            second = run_feed_batch(spec, raw, EXAMPLES, CsvFeedProvider(tape), now=now)
            self.assertEqual(second["days_processed_this_call"], 1)
            write([2, 3, 4, 5, 9])  # forgot Monday the 8th
            with self.assertRaisesRegex(ValueError, "Missing complete market sessions: 2024-01-08"):
                run_feed_batch(spec, raw, EXAMPLES, CsvFeedProvider(tape), now=now)


if __name__ == "__main__":
    unittest.main()
