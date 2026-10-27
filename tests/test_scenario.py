"""Scenario packages, generators, transforms, and suites."""

from __future__ import annotations

import csv
import json
import math
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path

from quantarium.data import load_daily_csv
from quantarium.scenario import (generate_scenario, load_scenario, slice_scenario,
                              transform_scenario)
from quantarium.suite import load_suite, run_suite, suite_report, write_suite_csv


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)
SPEC = {
    "name": "flat", "tags": ["sideways"], "seed": 3, "start_date": "2030-01-07",
    "symbols": ["AAA", "BBB"],
    "segments": [{"sessions": 30, "annual_drift": 0.1, "annual_volatility": 0.2},
                 {"sessions": 10, "annual_drift": -0.5, "annual_volatility": 0.4}],
}


def closes(directory: Path, symbol: str) -> list[float]:
    scenario = load_scenario(directory)
    days, tape, _ = load_daily_csv(scenario.prices_path, set(scenario.symbols))
    return [tape[day][symbol].close for day in days]


class ScenarioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.folder = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_generator_is_seeded_valid_and_uses_weekdays(self) -> None:
        first = generate_scenario(SPEC, self.folder / "a")
        second = generate_scenario(SPEC, self.folder / "b")
        self.assertEqual((self.folder / "a/prices.csv").read_bytes(),
                         (self.folder / "b/prices.csv").read_bytes())
        self.assertEqual(first.sessions, 40)
        self.assertEqual(first.kind, "synthetic")
        self.assertEqual(first.symbols, ("AAA", "BBB"))
        days, _, _ = load_daily_csv(first.prices_path, {"AAA", "BBB"})
        self.assertTrue(all(day.weekday() < 5 for day in days))
        other = generate_scenario({**SPEC, "seed": 4}, self.folder / "c")
        self.assertNotEqual(first.manifest_sha256, other.manifest_sha256)
        self.assertNotEqual(second.manifest_sha256, other.manifest_sha256)
        with self.assertRaises(FileExistsError):
            generate_scenario(SPEC, self.folder / "a")

    def test_tampered_scenario_is_refused(self) -> None:
        generate_scenario(SPEC, self.folder / "a")
        prices = self.folder / "a/prices.csv"
        prices.write_text(prices.read_text(encoding="utf-8").replace("2030-01-07", "2030-01-06", 1),
                          encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed since it was written"):
            load_scenario(self.folder / "a")

    def test_crlf_checkout_is_not_a_change(self) -> None:
        original = generate_scenario(SPEC, self.folder / "a")
        for name in ("prices.csv", "scenario.json"):
            path = self.folder / "a" / name
            lf = path.read_bytes().replace(b"\r\n", b"\n")
            path.write_bytes(lf.replace(b"\n", b"\r\n"))
        self.assertEqual(load_scenario(self.folder / "a").manifest_sha256,
                         original.manifest_sha256)

    def test_slice_keeps_range_and_entitled_actions(self) -> None:
        scenario = slice_scenario(
            EXAMPLES / "action_prices.csv", self.folder / "slice",
            date(2024, 1, 3), date(2024, 1, 5), name="slice", tags=("split",),
            actions=EXAMPLES / "actions.csv",
        )
        self.assertEqual(scenario.kind, "historical")
        self.assertEqual((scenario.start_date, scenario.end_date),
                         (date(2024, 1, 3), date(2024, 1, 5)))
        with (self.folder / "slice/actions.csv").open(encoding="utf-8") as source:
            rows = list(csv.DictReader(source))
        # The split (ex 01-04) and the dividend (ex 01-05, paid 01-08) both apply.
        self.assertEqual([row["kind"] for row in rows], ["split", "dividend"])
        self.assertEqual(scenario.provenance["method"], "slice")

    def test_shock_follows_its_declared_path(self) -> None:
        generate_scenario(SPEC, self.folder / "base")
        operation = {"op": "shock", "start_date": "2030-01-14", "sessions": 4,
                     "drawdown": 0.4, "recovery_sessions": 5, "recovery_fraction": 0.5}
        transform_scenario(self.folder / "base", {"name": "shocked", "operations": [operation]},
                           self.folder / "shocked")
        before, after = closes(self.folder / "base", "AAA"), closes(self.folder / "shocked", "AAA")
        start = 5  # 2030-01-14 is the sixth weekday from 2030-01-07
        ratios = [new / old for new, old in zip(after, before)]
        for index in range(start):
            self.assertAlmostEqual(ratios[index], 1, places=5)
        self.assertAlmostEqual(ratios[start + 3], 0.6, places=5)
        self.assertAlmostEqual(ratios[start + 8], 0.8, places=5)
        self.assertAlmostEqual(ratios[-1], 0.8, places=5)
        shocked = load_scenario(self.folder / "shocked")
        self.assertEqual(shocked.kind, "transformed")
        self.assertEqual(shocked.tags, ("sideways",))
        self.assertEqual(shocked.provenance["parent"], "flat")

    def test_volatility_and_drift_transforms(self) -> None:
        generate_scenario(SPEC, self.folder / "base")
        transform_scenario(self.folder / "base", {
            "name": "calm", "operations": [{"op": "scale_volatility", "factor": 0, "symbols": ["AAA"]}],
        }, self.folder / "calm")
        calm = closes(self.folder / "calm", "AAA")
        returns = [math.log(calm[i] / calm[i - 1]) for i in range(1, len(calm))]
        self.assertLess(max(returns) - min(returns), 1e-5)
        self.assertEqual(closes(self.folder / "calm", "BBB"), closes(self.folder / "base", "BBB"))
        transform_scenario(self.folder / "base", {
            "name": "drifted", "operations": [{"op": "add_drift", "annual": -0.252}],
        }, self.folder / "drifted")
        base, drifted = closes(self.folder / "base", "AAA"), closes(self.folder / "drifted", "AAA")
        self.assertAlmostEqual(drifted[-1] / base[-1], math.exp(-0.001 * (len(base) - 1)), places=5)

    def test_transform_refuses_corporate_actions(self) -> None:
        slice_scenario(EXAMPLES / "action_prices.csv", self.folder / "slice",
                       date(2024, 1, 2), date(2024, 1, 8), name="slice",
                       actions=EXAMPLES / "actions.csv")
        with self.assertRaisesRegex(ValueError, "corporate actions"):
            transform_scenario(self.folder / "slice", {"name": "x", "operations": [
                {"op": "add_drift", "annual": 0.1}]}, self.folder / "x")


class SuiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.folder = Path(self.temp.name)
        shutil.copy(EXAMPLES / "strategies.py", self.folder / "strategies.py")
        generate_scenario({**SPEC, "name": "up", "tags": ["bull"]}, self.folder / "up")
        generate_scenario({**SPEC, "name": "named", "tags": ["bull", "odd"],
                           "symbols": ["S1", "S2"]}, self.folder / "named")
        self.suite = {
            "name": "test_suite", "database_path": "suite.sqlite", "benchmark": "hold",
            "scenarios": ["up", {"path": "named", "symbol_map": {"S1": "AAA", "S2": "BBB"}}],
            "cells": [
                {"name": "hold", "mode": "simple", "symbols": ["AAA"], "initial_cash": 1000,
                 "strategies": [{"entrypoint": "strategies:BuyAndHold"}]},
                {"name": "pair", "mode": "complex", "symbols": ["AAA", "BBB"],
                 "initial_cash": 1000,
                 "strategies": [{"entrypoint": "strategies:EqualWeight"}]},
            ],
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(self, suite: dict) -> Path:
        path = self.folder / "suite.json"
        path.write_text(json.dumps(suite), encoding="utf-8")
        return path

    def test_suite_runs_every_scenario_and_compares_cells(self) -> None:
        path = self.write(self.suite)
        spec, raw = load_suite(path)
        result = run_suite(spec, raw, self.folder)
        self.assertEqual([run["scenario"] for run in result["runs"]], ["up", "named"])
        report = suite_report(spec)
        self.assertEqual(len(report["matrix"]), 4)
        hold = {row["scenario"]: row for row in report["matrix"] if row["cell"] == "hold"}
        self.assertEqual(hold["up"]["excess_return_pp"], 0)
        self.assertEqual(hold["named"]["kind"], "synthetic")
        self.assertIsNotNone(hold["up"]["annualized_volatility_pct"])
        bull = next(item for item in report["by_tag"] if item["tag"] == "bull" and item["cell"] == "hold")
        self.assertEqual(bull["scenarios"], 2)
        self.assertAlmostEqual(bull["mean_total_return_pct"],
                               (hold["up"]["total_return_pct"] + hold["named"]["total_return_pct"]) / 2)
        self.assertTrue(any("Synthetic" in caveat for caveat in report["caveats"]))
        with closing(sqlite3.connect(spec.database_path)) as connection:
            metadata = [json.loads(row[0]) for row in connection.execute(
                "SELECT metadata_json FROM runs ORDER BY created_utc")]
        self.assertEqual(metadata[1]["symbol_map"], {"S1": "AAA", "S2": "BBB"})
        write_suite_csv(self.folder / "matrix.csv", report)
        with (self.folder / "matrix.csv").open(encoding="utf-8") as source:
            rows = list(csv.DictReader(source))
        self.assertEqual(rows[2]["tags"], "bull;odd")

    def test_suite_refuses_missing_symbols_and_bad_maps(self) -> None:
        unmapped = {**self.suite, "scenarios": ["named"]}
        with self.assertRaisesRegex(ValueError, "does not provide AAA, BBB"):
            load_suite(self.write(unmapped))
        clash = {**self.suite, "scenarios": [{"path": "named", "symbol_map": {"S1": "S2"}}]}
        with self.assertRaisesRegex(ValueError, "maps two symbols to one"):
            load_suite(self.write(clash))

    def test_report_refuses_scenario_changed_after_run(self) -> None:
        spec, raw = load_suite(self.write(self.suite))
        run_suite(spec, raw, self.folder)
        generate_scenario({**SPEC, "name": "up", "tags": ["bull"], "seed": 99},
                          self.folder / "up", overwrite=True)
        spec, _ = load_suite(self.write(self.suite))
        with self.assertRaisesRegex(ValueError, "changed since its last run"):
            suite_report(spec)

    def test_example_suite_loads(self) -> None:
        spec, _ = load_suite(EXAMPLES / "suite.json")
        self.assertEqual({item.scenario.kind for item in spec.scenarios},
                         {"synthetic", "transformed"})


if __name__ == "__main__":
    unittest.main()


class ScenarioFeedTests(unittest.TestCase):
    def test_released_in_steps_matches_static_and_is_never_forward(self) -> None:
        from quantarium.audit import audit_replay
        from quantarium.analysis import read_analysis
        from quantarium.config import load_feed_config
        from quantarium.engine import run_static
        from quantarium.feed import run_feed_batch
        from quantarium.model import RunSpec
        from quantarium.scenario import ScenarioProvider

        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            folder = Path(temp)
            config = json.loads((EXAMPLES / "feed_scenario_experiment.json").read_text(encoding="utf-8"))
            config["scenario_path"] = str(EXAMPLES / "scenarios" / "bull_with_crash")
            config["database_path"] = str(folder / "feed.sqlite")
            path = folder / "feed.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            spec, raw = load_feed_config(path)
            scenario = load_scenario(Path(spec.scenario_path))
            provider = ScenarioProvider(scenario)
            for through in (date(2030, 3, 1), date(2030, 7, 1), None):
                report = run_feed_batch(spec, raw, EXAMPLES, provider, through=through)
            self.assertEqual(report["cells"][0]["last_date"], scenario.end_date.isoformat())
            static = RunSpec(str(scenario.prices_path), str(folder / "static.sqlite"), spec.slippage_bps,
                             spec.commission_per_order, spec.cells)
            run_id = run_static(static, "{}", EXAMPLES)
            audit = audit_replay(folder / "feed.sqlite", spec.feed_id, folder / "static.sqlite", run_id)
            self.assertTrue(audit["matched"], audit["mismatches"])
            analysis = read_analysis(folder / "feed.sqlite", "feed", spec.feed_id)
            self.assertEqual({row["phase"] for row in analysis["curve"]}, {"scenario"})
            self.assertTrue(all(cell["forward_sessions"] == 0 and cell["input"] == "scenario"
                                for cell in analysis["cells"]))
