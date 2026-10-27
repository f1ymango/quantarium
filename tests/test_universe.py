"""v0.9.1: symbols that list, delist, and change index membership during a Static run (ADR-0039)."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from quantarium.config import load_config, load_feed_config
from quantarium.engine import run_static
from quantarium.model import CellSpec, RunSpec, StrategySpec
from quantarium.scenario import load_scenario
from quantarium.suite import SuiteScenario, SuiteSpec, run_suite, suite_report
from quantarium.userdata import import_user_data

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
FICTION = EXAMPLES / "data" / "fictional_index"
STRATEGIES = ("BuyAndHold", "EqualWeight", "RecentLeader", "TrendFollower")


def events(database: Path, run_id: str, cell: str | None = None) -> list[tuple]:
    query = "SELECT cell_name, sequence, date, kind, payload_json FROM events WHERE run_id=?"
    args: tuple = (run_id,)
    if cell:
        query += " AND cell_name=?"
        args += (cell,)
    with closing(sqlite3.connect(database)) as db:
        return db.execute(query + " ORDER BY cell_name, sequence", args).fetchall()


def cell(name: str, symbols: tuple[str, ...], strategy: str = "EqualWeight", **extra) -> CellSpec:
    mode = "simple" if len(symbols) == 1 else "complex"
    return CellSpec(name, mode, symbols, 100_000, (StrategySpec(f"strategies:{strategy}", 1, {}),), **extra)


class UniverseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_spec(self, spec: RunSpec) -> tuple[Path, str]:
        return Path(spec.database_path), run_static(spec, "{}", EXAMPLES)

    def test_a_fixed_universe_replays_exactly_as_before(self) -> None:
        for scenario in ("steady_bull", "bull_with_crash", "sideways_chop", "recession_bear"):
            prices = EXAMPLES / "scenarios" / scenario / "prices.csv"
            symbols = tuple(sorted({line.split(",")[1] for line in prices.read_text().splitlines()[1:]}))
            cells = tuple(cell(name, symbols if name != "BuyAndHold" else symbols[:1], name, max_previous_volume_participation=0.5)
                          for name in STRATEGIES)
            base = RunSpec(str(prices), str(self.folder / f"{scenario}-classic.sqlite"), 5, 1, cells)
            changing = replace(base, database_path=str(self.folder / f"{scenario}-universe.sqlite"),
                               changing_universe=True)
            classic = self.run_spec(base)
            universe = self.run_spec(changing)
            with self.subTest(scenario=scenario):
                self.assertEqual(events(*classic), events(*universe))

    def fictional(self, *cells: CellSpec, **extra) -> tuple[Path, str]:
        spec = RunSpec(str(FICTION / "prices.csv"), str(self.folder / "fiction.sqlite"), 0, 0, cells,
                       changing_universe=True, delistings_path=str(FICTION / "delistings.csv"),
                       membership_path=str(FICTION / "membership.csv"), **extra)
        return self.run_spec(spec)

    def test_late_listings_join_and_delistings_pay_out(self) -> None:
        symbols = ("ALFA", "BRVO", "CHRL", "DLTA", "ECHO", "FXTR")
        database, run_id = self.fictional(cell("all", symbols))
        trail = events(database, run_id)
        signals = [(day, json.loads(payload)) for _, _, day, kind, payload in trail if kind == "signal"]
        self.assertNotIn("DLTA", signals[0][1]["target_weights"])
        listed = next(day for day, signal in signals if "DLTA" in signal["target_weights"])
        self.assertEqual(listed, "2031-03-03")  # its first session
        delisted = {json.loads(payload)["symbol"]: (day, json.loads(payload)) for _, _, day, kind, payload in trail
                    if kind == "delisted"}
        self.assertEqual(set(delisted), {"ECHO", "FXTR"})
        echo_day, echo = delisted["ECHO"]
        self.assertEqual((echo_day, echo["price"], echo["reason"]), ("2031-05-12", 0.05, "bankruptcy"))
        self.assertAlmostEqual(echo["proceeds"], echo["shares"] * 0.05)
        _, fxtr = delisted["FXTR"]
        self.assertAlmostEqual(fxtr["price"], round(fxtr["last_close"] * 1.2, 4))
        valuations = [json.loads(payload) for *_, kind, payload in trail if kind == "valuation"]
        self.assertTrue(all(value["positions"]["ECHO"] == 0 for value in valuations[90:]))
        self.assertNotIn("ECHO", valuations[-1]["prices"])

    def test_an_index_cell_holds_only_members_as_of_each_close(self) -> None:
        symbols = ("ALFA", "BRVO", "CHRL", "DLTA", "ECHO", "FXTR")
        database, run_id = self.fictional(cell("fic5", symbols, index="FIC5"))
        targets = {day: set(json.loads(payload)["target_weights"])
                   for _, _, day, kind, payload in events(database, run_id) if kind == "signal"}
        self.assertEqual(targets["2031-01-06"], {"ALFA", "BRVO", "CHRL", "ECHO", "FXTR"})
        self.assertNotIn("CHRL", targets["2031-04-14"])          # left on 2031-04-11
        self.assertIn("DLTA", targets["2031-04-14"])             # joined on 2031-04-14
        self.assertIn("CHRL", targets["2031-06-30"])             # rejoined
        self.assertNotIn("FXTR", targets["2031-06-30"])

    def test_weight_outside_the_universe_stays_in_cash_and_is_recorded(self) -> None:
        database, run_id = self.fictional(cell("echo", ("ECHO",), "BuyAndHold"))
        trail = events(database, run_id)
        signals = [json.loads(payload) for *_, kind, payload in trail if kind == "signal"]
        self.assertTrue(any(signal.get("empty_universe") for signal in signals))
        report = [json.loads(payload) for *_, kind, payload in trail if kind == "valuation"]
        self.assertEqual(report[-1]["positions"]["ECHO"], 0)
        self.assertGreater(report[-1]["cash"], 0)

    def test_a_starting_holding_must_trade_on_the_first_session(self) -> None:
        late = replace(cell("late", ("DLTA",), "BuyAndHold"), initial_positions={"DLTA": (10, None)})
        database, run_id = self.fictional(late)
        with closing(sqlite3.connect(database)) as db:
            status, error = db.execute("SELECT status, error FROM cells WHERE run_id=?", (run_id,)).fetchone()
        self.assertEqual(status, "failed")
        self.assertIn("does not trade on 2031-01-06", error)

    def test_without_the_switch_a_changing_universe_is_still_refused(self) -> None:
        spec = RunSpec(str(FICTION / "prices.csv"), str(self.folder / "plain.sqlite"), 0, 0,
                       (cell("all", ("ALFA", "DLTA")),))
        with self.assertRaisesRegex(ValueError, "Missing daily bars"):
            run_static(spec, "{}", EXAMPLES)

    def test_configs_switch_it_on_and_feed_refuses_index_cells(self) -> None:
        config = {"data_path": str(FICTION / "prices.csv"), "database_path": str(self.folder / "c.sqlite"),
                  "membership_path": str(FICTION / "membership.csv"), "strategy_root": str(EXAMPLES),
                  "cells": [{"name": "fic5", "mode": "complex", "symbols": ["ALFA", "BRVO", "DLTA"],
                             "initial_cash": 10_000, "index": "FIC5",
                             "strategies": [{"entrypoint": "strategies:EqualWeight"}]}]}
        path = self.folder / "config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        spec, _ = load_config(path)
        self.assertTrue(spec.changing_universe)
        self.assertEqual(spec.cells[0].index, "FIC5")
        del config["membership_path"]
        path.write_text(json.dumps(config), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "needs membership_path"):
            load_config(path)
        feed = {**config, "feed_id": "f", "provider": "csv", "start_date": "2031-01-06"}
        path.write_text(json.dumps(feed), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "fixed set of symbols"):
            load_feed_config(path)
        config["cells"][0]["index"] = "../x"
        path.write_text(json.dumps(config), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "index must name"):
            load_config(path)

    def test_an_imported_scenario_runs_in_a_suite(self) -> None:
        scenario, _ = import_user_data(FICTION, self.folder / "scenario", name="fiction")
        cells = (cell("fic5", ("ALFA", "BRVO", "CHRL", "DLTA", "ECHO", "FXTR"), index="FIC5"),)
        spec = SuiteSpec("fiction", str(self.folder / "suite.sqlite"), 0, 0, cells,
                         (SuiteScenario(load_scenario(scenario.directory), {}),), None)
        run_suite(spec, "{}", EXAMPLES)
        row = suite_report(spec)["matrix"][0]
        self.assertEqual(row["status"], "completed", row.get("error"))


if __name__ == "__main__":
    unittest.main()
