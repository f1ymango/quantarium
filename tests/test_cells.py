"""Cell library, script discovery, deployment, and holdings for the frontend."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from quantarium.api import Api, ApiError
from quantarium.cells import CellLibrary
from quantarium.holdings import read_holdings
from quantarium.scripts import strategy_classes
from quantarium.workspace import Workspace


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)


def sleeve(name: str, allocation: float = 1, **parameters) -> dict:
    return {"script": "strategies/strategies.py", "class": name,
            "allocation": allocation, "parameters": parameters}


class CellTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.root = Path(self.temp.name)
        (self.root / "strategies").mkdir()
        shutil.copy(EXAMPLES / "strategies.py", self.root / "strategies/strategies.py")
        (self.root / "data").mkdir()
        shutil.copy(EXAMPLES / "sample_daily.csv", self.root / "data/sample_daily.csv")
        shutil.copytree(EXAMPLES / "scenarios", self.root / "scenarios")
        self.workspace = Workspace(self.root)
        self.library = CellLibrary(self.workspace)
        self.api = Api(self.workspace)

    def tearDown(self) -> None:
        self.api.close()
        self.temp.cleanup()

    def ready(self, name: str, symbols: list[str], *sleeves: dict, mode: str = "simple") -> dict:
        record = self.library.create(name)
        return self.library.save(record["id"], spec={
            "mode": mode, "symbols": symbols, "initial_cash": 10000,
            "sleeves": list(sleeves) or [sleeve("BuyAndHold")]})

    def run_job(self, action: str, params: dict) -> dict:
        job = self.api.wait(self.api.submit(action, params)["id"])
        self.assertEqual(job["status"], "succeeded", job["error"])
        return job["result"]

    def test_scripts_are_listed_by_parsing(self) -> None:
        (self.root / "strategies/trap.py").write_text(
            "raise SystemExit('this file must not run')\n"
            "class Trap:\n    def __init__(self, window: int = 5, label='x', model=None):\n"
            "        pass\n    def on_bar(self, context):\n        return 1.0\n", encoding="utf-8")
        classes = {item["name"]: item for item in strategy_classes(self.root / "strategies/trap.py")}
        self.assertEqual([p["name"] for p in classes["Trap"]["parameters"]], ["window", "label", "model"])
        self.assertEqual(classes["Trap"]["parameters"][0]["default"], 5)
        listed = {item["key"]: item for item in self.workspace.scripts()}
        names = {entry["name"] for entry in listed["strategies/strategies.py"]["classes"]}
        self.assertTrue({"BuyAndHold", "TrendFollower", "AlternatingDays"} <= names)
        alternating = next(entry for entry in listed["strategies/strategies.py"]["classes"]
                           if entry["name"] == "AlternatingDays")
        self.assertTrue(alternating["stateful"])

    def test_drafts_names_and_versions(self) -> None:
        first = self.library.create()
        second = self.library.create()
        self.assertEqual((first["name"], second["name"]), ("Unnamed cell", "Unnamed cell 2"))
        self.assertIn("Add at least one holding", self.library.check(first))
        with self.assertRaisesRegex(ValueError, "already named"):
            self.library.save(second["id"], name="unnamed CELL")
        record = self.ready("Hold AAA", ["aaa", "AAA"])
        self.assertEqual(record["spec"]["symbols"], ["AAA"])
        self.assertEqual(self.library.check(record), [])
        edited = self.library.save(record["id"], spec={**record["spec"], "initial_cash": 5000})
        self.assertEqual(edited["version"], 1)  # never deployed: edited in place
        self.library.record_deployment(record["id"], {"kind": "static", "db": "x", "id": "y"})
        bumped = self.library.save(record["id"], spec={**edited["spec"], "initial_cash": 7000})
        self.assertEqual(bumped["version"], 2)
        self.assertEqual(bumped["history"][0]["spec"]["initial_cash"], 5000)
        renamed = self.library.save(record["id"], name="Hold AAA v2")
        self.assertEqual(renamed["version"], 2)  # renaming alone is not a new version
        with self.assertRaisesRegex(ValueError, "has results"):
            self.library.delete(record["id"])

    def test_cell_library_preserves_starting_holdings_and_information_boundary(self) -> None:
        record = self.ready("Existing position", ["AAA"])
        saved = self.library.save(record["id"], spec={
            **record["spec"],
            "initial_positions": {"AAA": {"shares": 5, "cost": 80}},
            "information_end": "2024-01-03",
        })
        entry, _ = self.library.engine_cell(saved)
        self.assertEqual(entry["initial_positions"], {"AAA": {"shares": 5, "cost": 80}})
        self.assertEqual(entry["information_end"], "2024-01-03")
        self.assertEqual(self.library.check(saved), [])

    def test_unknown_script_and_mixed_folders_are_reported(self) -> None:
        record = self.ready("Bad", ["AAA"], sleeve("Missing"))
        self.assertIn("Script strategies/strategies.py:Missing was not found", self.library.check(record))
        (self.root / "other").mkdir()
        shutil.copy(EXAMPLES / "strategies.py", self.root / "other/strategies.py")
        mixed = self.ready("Mixed", ["AAA", "BBB"], sleeve("EqualWeight", 0.5),
                           {**sleeve("EqualWeight", 0.5), "script": "other/strategies.py"},
                           mode="complex")
        self.assertIn("All of a cell's scripts must be in one folder", self.library.check(mixed))

    def test_deploy_static_then_cards_and_holdings(self) -> None:
        hold = self.ready("Hold", ["AAA"])
        basket = self.ready("Basket", ["AAA", "BBB"], sleeve("EqualWeight", 0.6),
                            sleeve("RecentLeader", 0.4), mode="complex")
        result = self.run_job("deploy-static", {"cells": [hold["id"], basket["id"]],
                                                "data": "data/sample_daily.csv"})
        self.assertTrue(result["config"].startswith("configs/generated/static-"))
        cards = {card["name"]: card for card in self.api.get("cells", {})}
        self.assertEqual(cards["Hold"]["status"], "results")
        self.assertEqual(cards["Hold"]["deployments"]["static"], 1)
        self.assertEqual(cards["Basket"]["result"]["evidence"], "historical replay")
        self.assertGreater(len(cards["Basket"]["result"]["spark"]), 1)
        holdings = self.api.get("holdings", {"db": result["db"], "kind": "static",
                                             "id": result["id"], "cell": "Basket"})
        self.assertEqual(holdings["view"], "chart")
        self.assertEqual(set(holdings["series"]["prices"]), {"AAA", "BBB"})
        self.assertTrue(holdings["fills"])
        self.assertAlmostEqual(sum(row["weight_pct"] for row in holdings["summary"])
                               + 100 * holdings["series"]["cash"][-1] / holdings["series"]["equity"][-1],
                               100, places=6)
        detail = self.api.get(f"cells/{hold['id']}", {})
        self.assertEqual(len(detail["results"]), 1)

    def test_deploy_suite_maps_symbols_and_reports(self) -> None:
        trend = self.ready("Trend", ["XYZ"], sleeve("TrendFollower", window=20))
        with self.assertRaisesRegex(ApiError, "nonempty"):
            self.api.submit("deploy-suite", {"cells": [trend["id"]], "scenarios": []})
        job = self.api.wait(self.api.submit("deploy-suite", {
            "cells": [trend["id"]], "scenarios": ["scenarios/steady_bull"]})["id"])
        self.assertEqual(job["status"], "failed")  # two symbols in the scenario, one in the cell
        self.assertIn("give a symbol map", job["error"]["message"])
        result = self.run_job("deploy-suite", {
            "cells": [trend["id"]], "scenarios": ["scenarios/steady_bull", "scenarios/bull_with_crash"],
            "symbol_maps": {"scenarios/steady_bull": {"AAA": "XYZ"},
                            "scenarios/bull_with_crash": {"AAA": "XYZ"}}})
        self.assertEqual(len(result["runs"]), 2)
        card = next(card for card in self.api.get("cells", {}) if card["name"] == "Trend")
        self.assertEqual(card["result"]["evidence"], "scenario suite")
        self.assertEqual(card["result"]["scenarios"], 2)
        report = self.api.get("suite-report", {"config": result["config"]})
        self.assertEqual(len(report["matrix"]), 2)

    def test_deploy_feed_from_a_scenario_then_add_a_cell(self) -> None:
        first = self.ready("First", ["AAA"], sleeve("TrendFollower"))
        result = self.run_job("deploy-feed", {
            "cells": [first["id"]], "feed_id": "lab", "provider": "scenario",
            "source": "scenarios/bull_with_crash", "start_date": "2030-01-07",
            "through": "2030-03-01"})
        self.assertEqual(result["days_processed"], 40)
        second = self.ready("Second", ["BBB"])
        self.run_job("deploy-feed", {"cells": [second["id"]], "feed_id": "lab"})
        config = json.loads((self.root / result["config"]).read_text(encoding="utf-8"))
        self.assertEqual([cell["name"] for cell in config["cells"]], ["First", "Second"])
        card = next(card for card in self.api.get("cells", {}) if card["name"] == "First")
        self.assertEqual(card["result"]["evidence"], "scenario")
        clash = self.api.wait(self.api.submit("deploy-feed", {"cells": [second["id"]],
                                                              "feed_id": "lab"})["id"])
        self.assertIn("Already in lab", clash["error"]["message"])

    def test_arena_and_scoped_cards(self) -> None:
        trend = self.ready("Trend", ["AAA"], sleeve("TrendFollower"))
        self.run_job("deploy-static", {"cells": [trend["id"]], "data": "data/sample_daily.csv"})
        self.run_job("deploy-feed", {"cells": [trend["id"]], "feed_id": "arena_test",
                                     "provider": "scenario", "source": "scenarios/steady_bull",
                                     "start_date": "2030-01-07", "through": "2030-02-01"})
        static_card = self.api.get("cells", {"scope": "static"})[0]
        feed_card = self.api.get("cells", {"scope": "feed"})[0]
        self.assertEqual(static_card["result"]["evidence"], "historical replay")
        self.assertEqual(feed_card["result"]["evidence"], "scenario")
        with self.assertRaises(ApiError):
            self.api.get("cells", {"scope": "moon"})
        arena = self.api.get("arena", {})
        self.assertEqual(len(arena), 1)
        experiment = arena[0]
        self.assertEqual(experiment["id"], "arena_test")
        self.assertTrue(experiment["config"].startswith("configs/generated/feed-"))
        self.assertEqual(experiment["cells"][0]["cell_id"], trend["id"])
        self.assertEqual(experiment["cells"][0]["mode"], "simple")
        self.assertGreater(len(experiment["cells"][0]["spark"]), 1)

    def test_commands_manage_the_library(self) -> None:
        a = self.api.command("cell-create", {})
        b = self.api.command("cell-create", {"name": "Keeper"})
        copies = self.api.command("cell-duplicate", {"ids": [b["id"]]})
        self.assertEqual(copies[0]["name"], "Keeper copy")
        self.api.command("cell-archive", {"ids": [b["id"]]})
        self.assertNotIn(b["id"], {card["id"] for card in self.api.get("cells", {})})
        self.assertIn(b["id"], {card["id"] for card in self.api.get("cells", {"archived": "1"})})
        self.api.command("cell-restore", {"ids": [b["id"]]})
        self.api.command("cell-delete", {"ids": [a["id"]]})
        self.assertNotIn(a["id"], {card["id"] for card in self.api.get("cells", {})})
        with self.assertRaises(ApiError):
            self.api.command("cell-explode", {})
        with self.assertRaises(FileNotFoundError):
            self.api.get("cells/../../etc", {})

    def test_holdings_views_by_size(self) -> None:
        symbols = [f"S{index:02d}" for index in range(12)]
        spec = {"name": "wide", "tags": [], "seed": 1, "start_date": "2030-01-07",
                "symbols": symbols, "segments": [{"sessions": 20, "annual_drift": 0.1,
                                                  "annual_volatility": 0.2}]}
        self.run_job("scenario-generate", {"spec": spec, "output": "scenarios/wide"})
        cell = self.ready("Wide", symbols, sleeve("EqualWeight"), mode="complex")
        result = self.run_job("deploy-suite", {"cells": [cell["id"]], "scenarios": ["scenarios/wide"]})
        run_id = result["runs"][0]["run_id"]
        holdings = read_holdings(self.root / "runs/suites.sqlite", "static", run_id, "Wide")
        self.assertEqual(holdings["view"], "tiles")
        self.assertNotIn("prices", holdings["series"])
        self.assertEqual(set(holdings["series"]["sparks"]), set(symbols))
        self.assertEqual(len(holdings["summary"]), 12)


if __name__ == "__main__":
    unittest.main()
