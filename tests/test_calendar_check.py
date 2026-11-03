"""v1.1.0: price files checked against the NYSE calendar, and slices that need coverage only inside them."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from quantarium.config import load_config
from quantarium.data import check_calendar
from quantarium.engine import run_static
from quantarium.market_calendar import sessions
from quantarium.scenario import load_scenario, slice_scenario

ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)
JULY = sorted(sessions(date(2024, 7, 1), date(2024, 7, 12)))  # July 4 is a holiday


def write_prices(path: Path, rows: dict[str, list[date]]) -> Path:
    """Invented bars: each symbol trades on its listed days at 100 + the day's index."""
    lines = ["date,symbol,open,high,low,close,volume"]
    for symbol, days in rows.items():
        lines += [f"{day},{symbol},{100 + i},{101 + i},{99 + i},{100 + i},1000" for i, day in enumerate(days)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class CheckCalendarTests(unittest.TestCase):
    def test_matching_sessions_pass_and_none_skips(self) -> None:
        check_calendar(JULY, "nyse")
        check_calendar(JULY + [date(2024, 7, 13)], None)

    def test_holiday_bars_and_skipped_sessions_are_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "2024-07-04, which is not an NYSE session"):
            check_calendar(sorted(JULY + [date(2024, 7, 4)]), "nyse")
        with self.assertRaisesRegex(ValueError, "No bars on NYSE session 2024-07-05"):
            check_calendar([day for day in JULY if day != date(2024, 7, 5)], "nyse")

    def test_unknown_calendars_and_early_years_are_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown calendar"):
            check_calendar(JULY, "lse")
        with self.assertRaisesRegex(ValueError, "covers 1998 on"):
            check_calendar([date(1997, 12, 31), date(1998, 1, 2)], "nyse")


class StaticCalendarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.folder = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def config(self, days: list[date], **extra) -> Path:
        write_prices(self.folder / "prices.csv", {"AAA": days})
        path = self.folder / "experiment.json"
        path.write_text(json.dumps({
            "data_path": "prices.csv", "database_path": "run.sqlite", "strategy_root": str(ROOT / "examples"),
            "cells": [{"name": "hold", "mode": "simple", "symbols": ["AAA"], "initial_cash": 1000,
                       "strategies": [{"entrypoint": "strategies:BuyAndHold"}]}], **extra}), encoding="utf-8")
        return path

    def run_config(self, path: Path) -> str:
        spec, raw = load_config(path)
        return run_static(spec, raw, ROOT / "examples")

    def test_a_holiday_bar_is_refused_only_when_the_config_names_the_calendar(self) -> None:
        days = sorted(JULY + [date(2024, 7, 4)])
        self.assertTrue(self.run_config(self.config(days)))
        with self.assertRaisesRegex(ValueError, "not an NYSE session"):
            self.run_config(self.config(days, calendar="nyse"))
        self.assertTrue(self.run_config(self.config(JULY, calendar="nyse")))

    def test_unknown_calendar_values_are_refused(self) -> None:
        for value in ("NYSE", "lse", 1, True):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "calendar must be one of"):
                load_config(self.config(JULY, calendar=value))


class SliceWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.folder = Path(self.temp.name)
        # BBB starts trading a week after AAA, as a fund listed later or a provider history that starts later would.
        days = sorted(sessions(date(2024, 6, 24), date(2024, 7, 12)))
        self.source = write_prices(self.folder / "source.csv",
                                   {"AAA": days, "BBB": [day for day in days if day >= date(2024, 7, 1)]})

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_symbols_need_bars_only_inside_the_slice(self) -> None:
        scenario = slice_scenario(self.source, self.folder / "later", date(2024, 7, 1), date(2024, 7, 12),
                                  name="later")
        self.assertEqual((scenario.symbols, scenario.sessions), (("AAA", "BBB"), len(JULY)))
        with self.assertRaisesRegex(ValueError, "Missing daily bars on 2024-06-24: BBB"):
            slice_scenario(self.source, self.folder / "early", date(2024, 6, 24), date(2024, 7, 12), name="early")

    def test_invalid_bars_outside_the_slice_are_still_refused(self) -> None:
        text = self.source.read_text(encoding="utf-8").replace("2024-06-24,AAA,100,101,99,", "2024-06-24,AAA,100,101,102,")
        self.source.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Inconsistent OHLC"):
            slice_scenario(self.source, self.folder / "later", date(2024, 7, 1), date(2024, 7, 12), name="later")

    def test_calendar_option_checks_and_records_the_slice(self) -> None:
        holiday = date(2024, 7, 4)
        days = sorted(JULY + [holiday])
        source = write_prices(self.folder / "holiday.csv", {"AAA": days})
        with self.assertRaisesRegex(ValueError, "2024-07-04, which is not an NYSE session"):
            slice_scenario(source, self.folder / "checked", JULY[0], JULY[-1], name="checked", calendar="nyse")
        self.assertFalse((self.folder / "checked").exists())
        slice_scenario(source, self.folder / "unchecked", JULY[0], JULY[-1], name="unchecked")
        clean = slice_scenario(self.source, self.folder / "clean", JULY[0], JULY[-1], name="clean", calendar="nyse")
        manifest = json.loads((self.folder / "clean" / "scenario.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["provenance"]["calendar"], "nyse")
        self.assertEqual(load_scenario(self.folder / "clean").sessions, clean.sessions)
        self.assertEqual(clean.start_date, JULY[0])


if __name__ == "__main__":
    unittest.main()
