"""The first-run tutorial as a state: an empty private workspace, then one new cell."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from quantarium.api import Api
from quantarium.onboarding import STARTER_FILE
from quantarium.workspace import Workspace


ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)


class OnboardingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.root = Path(self.temp.name)
        self.api = Api(Workspace(self.root))

    def tearDown(self) -> None:
        self.api.close()
        self.temp.cleanup()

    def test_new_workspace_is_pending_and_empty_even_beside_examples(self) -> None:
        shutil.copytree(ROOT / "examples", self.root / "examples")
        state = self.api.get("onboarding", {})
        self.assertEqual(state["status"], "pending")
        self.assertIsNone(state["starter_script"])
        overview = self.api.get("workspace", {})
        self.assertEqual(overview["scenarios"], [])
        self.assertEqual(overview["data_files"], [])
        self.assertEqual(overview["configs"], [])
        self.assertEqual(self.api.get("cells", {}), [])

    def test_start_enters_an_empty_tutorial_state_that_resumes(self) -> None:
        self.api.command("cell-create", {"name": "Mine"})
        (self.root / "scenarios" / "mine").mkdir(parents=True)
        state = self.api.command("onboarding-start", {})
        self.assertEqual(state["status"], "active")
        self.assertEqual(state["names"], {"scenario": "first_market", "cell": "My first cell",
                                          "feed": "first_forward", "script": STARTER_FILE,
                                          "database": "runs/tutorial.sqlite"})
        self.assertEqual(self.api.get("cells", {}), [])
        self.assertEqual(self.api.get("workspace", {})["scenarios"], [])
        scripts = {item["key"]: item for item in self.api.get("scripts", {})}
        classes = {entry["name"] for entry in scripts[STARTER_FILE]["classes"]}
        self.assertTrue({"BuyAndHold", "TrendFollower", "EqualWeight", "RecentLeader"} <= classes)
        self.assertFalse((self.root / STARTER_FILE).exists())
        cell = self.api.command("cell-create", {})
        self.api.command("onboarding-step", {"step": 7, "cell_id": cell["id"]})
        self.assertFalse((self.root / "cells" / f"{cell['id']}.json").exists())
        reread = Api(Workspace(self.root))
        try:
            state = reread.get("onboarding", {})
            self.assertEqual([card["id"] for card in reread.get("cells", {})], [cell["id"]])
        finally:
            reread.close()
        self.assertEqual((state["step"], state["cell_id"]), (7, cell["id"]))
        with self.assertRaises(FileNotFoundError):
            self.api.command("onboarding-step", {"step": 8, "cell_id": "c_0000000000"})

    def test_finish_and_skip(self) -> None:
        with self.assertRaisesRegex(ValueError, "not running"):
            self.api.command("onboarding-step", {"step": 1})
        skipped = self.api.command("onboarding-finish", {"status": "skipped"})
        self.assertEqual(skipped["status"], "skipped")
        self.assertTrue((self.root / STARTER_FILE).is_file())
        with self.assertRaisesRegex(ValueError, "completed or skipped"):
            self.api.command("onboarding-finish", {"status": "abandoned"})
        self.api.command("onboarding-start", {})
        with self.assertRaisesRegex(ValueError, "with a cell"):
            self.api.command("onboarding-finish", {"status": "completed"})

    def test_leaving_partway_leaves_the_workspace_as_it_was(self) -> None:
        self.api.command("cell-create", {"name": "Mine"})
        before = self.files()
        names = self.api.command("onboarding-start", {})["names"]
        self.wait(self.api.submit("scenario-generate", {
            "spec": self.scenario_spec(names), "output": f"scenarios/{names['scenario']}"}))
        self.api.command("cell-create", {})
        self.api.command("onboarding-finish", {"status": "skipped"})
        self.assertEqual(self.files() - {STARTER_FILE}, before)
        self.assertEqual([card["name"] for card in self.api.get("cells", {})], ["Mine"])
        self.assertFalse((self.root / ".quantarium" / "tutorial").exists())

    def test_replay_names_avoid_everything_in_the_workspace(self) -> None:
        self.api.command("cell-create", {"name": "My first cell"})
        (self.root / "scenarios" / "first_market").mkdir(parents=True)
        # Only the Feed database is left; its generated config was deleted.
        (self.root / "runs").mkdir()
        (self.root / "runs" / "feed-first-forward.sqlite").write_bytes(b"")
        (self.root / "runs" / "tutorial.sqlite").write_bytes(b"")
        state = self.api.command("onboarding-start", {})
        self.assertEqual(state["names"], {"scenario": "first_market_2", "cell": "My first cell 2",
                                          "feed": "first_forward_2", "script": STARTER_FILE,
                                          "database": "runs/tutorial_2.sqlite"})

    def test_an_edited_starter_is_kept_and_the_tutorial_uses_its_own_copy(self) -> None:
        target = self.root / STARTER_FILE
        target.parent.mkdir(parents=True)
        target.write_text("# my own edits\n", encoding="utf-8")
        names = self.complete_tutorial()
        self.assertEqual(names["script"], "strategies/starter_2.py")
        self.assertEqual(target.read_text(encoding="utf-8"), "# my own edits\n")
        self.assertEqual(self.api.get("cells", {})[0]["problems"], [])

    def test_tutorial_ignores_custom_scan_folders(self) -> None:
        (self.root / "quantarium-workspace.json").write_text(json.dumps({"scenarios": ["markets"]}),
                                                          encoding="utf-8")
        api = Api(Workspace(self.root))
        try:
            names = api.command("onboarding-start", {})["names"]
            done = api.wait(api.submit("scenario-generate", {
                "spec": self.scenario_spec(names), "output": f"scenarios/{names['scenario']}"})["id"])
            self.assertEqual(done["status"], "succeeded", done["error"])
            listed = [item["name"] for item in api.get("workspace", {})["scenarios"]]
            self.assertEqual(listed, [names["scenario"]])
        finally:
            api.close()

    def test_a_run_from_an_older_version_starts_fresh(self) -> None:
        state_file = self.root / ".quantarium" / "onboarding.json"
        state_file.parent.mkdir(parents=True)
        state_file.write_text(json.dumps({"status": "active", "step": 4, "cell_id": None,
                                          "names": {"cell": "My first cell"}}), encoding="utf-8")
        self.assertEqual(self.api.get("onboarding", {})["status"], "pending")
        self.assertEqual(self.api.get("cells", {}), [])

    def test_picker_keeps_scripts_used_by_existing_cells(self) -> None:
        shutil.copytree(ROOT / "examples", self.root / "examples")
        cell = self.api.command("cell-create", {"name": "Old"})
        self.api.command("cell-save", {"id": cell["id"], "spec": {
            "mode": "simple", "symbols": ["AAA"], "initial_cash": 1000,
            "sleeves": [{"script": "examples/strategies.py", "class": "BuyAndHold"}]}})
        keys = {item["key"] for item in self.api.get("scripts", {})}
        self.assertIn("examples/strategies.py", keys)

    def test_finishing_adds_one_working_cell_to_the_workspace(self) -> None:
        self.api.command("cell-create", {"name": "Mine"})
        names = self.complete_tutorial(with_lab=True)
        cards = {card["name"]: card for card in self.api.get("cells", {})}
        self.assertEqual(set(cards), {"Mine", "My first cell"})
        self.assertEqual(cards["My first cell"]["deployments"], {"static": 0, "suite": 1, "feed": 1})
        self.assertTrue((self.root / STARTER_FILE).is_file())
        scenarios = [item["name"] for item in self.api.get("workspace", {})["scenarios"]]
        self.assertIn(names["scenario"], scenarios)
        self.assertFalse((self.root / ".quantarium" / "tutorial").exists())
        self.assertFalse((self.root / "runs" / "lab.sqlite").exists())
        self.assertEqual(self.api.get("lab", {}), [])
        # The promoted experiment keeps working in the real workspace.
        arena = self.api.get("arena", {})
        self.assertEqual([experiment["id"] for experiment in arena], [names["feed"]])
        result = self.wait(self.api.submit("feed-batch", {"config": arena[0]["config"]}))
        self.assertGreater(result["days_processed_this_call"], 0)
        # A replay starts empty again and adds a second cell under new names.
        replay = self.complete_tutorial()
        self.assertEqual(replay["cell"], "My first cell 2")
        self.assertEqual(len(self.api.get("cells", {})), 3)

    def complete_tutorial(self, with_lab: bool = False) -> dict:
        names = self.api.command("onboarding-start", {})["names"]
        self.assertEqual(self.api.get("cells", {}), [])
        scenario_key = f"scenarios/{names['scenario']}"
        self.wait(self.api.submit("scenario-generate", {"spec": self.scenario_spec(names),
                                                        "output": scenario_key}))
        cell = self.api.command("cell-create", {})
        self.api.command("onboarding-step", {"step": 3, "cell_id": cell["id"]})
        self.api.command("cell-save", {"id": cell["id"], "name": names["cell"], "spec": {
            "mode": "simple", "symbols": ["AAA"], "initial_cash": 10000,
            "sleeves": [{"script": names["script"], "class": "TrendFollower",
                         "parameters": {"window": 10}}]}})
        self.wait(self.api.submit("deploy-suite", {"cells": [cell["id"]], "scenarios": [scenario_key]}))
        self.wait(self.api.submit("deploy-feed", {
            "cells": [cell["id"]], "feed_id": names["feed"], "provider": "scenario",
            "source": scenario_key, "start_date": "2030-01-07", "through": "2030-03-01"}))
        if with_lab:
            self.run_tutorial_lab(cell["id"], scenario_key)
        self.api.command("onboarding-finish", {"status": "completed"})
        return names

    def run_tutorial_lab(self, cell_id: str, scenario_key: str) -> None:
        params = {"idea": "tutorial_first_cell", "name": "First attempt", "cells": [cell_id],
                  "scenarios": [scenario_key],
                  "pass_rule": {"min_mean_excess_return_pp": 0, "max_drawdown_pct": None,
                                "min_sessions": 60}}
        first = self.api.command("lab-register", params)
        self.assertEqual(first["attempt_number"], 1)
        first_result = self.wait(self.api.submit("lab-run", {"id": first["id"]}))
        self.assertFalse(first_result["result"]["verdict"]["used_data"])
        second = self.api.command("lab-register", {**params, "name": "Second attempt"})
        self.assertEqual(second["attempt_number"], 2)
        second_result = self.wait(self.api.submit("lab-run", {"id": second["id"]}))
        self.assertTrue(second_result["result"]["verdict"]["used_data"])
        self.assertTrue(any("already used related scenario data" in item
                            for item in second_result["result"]["verdict"]["caveats"]))
    @staticmethod
    def scenario_spec(names: dict) -> dict:
        return {"name": names["scenario"], "seed": 2026, "start_date": "2030-01-07",
                "symbols": ["AAA"], "segments": [
                    {"sessions": 60, "annual_drift": 0.3, "annual_volatility": 0.2},
                    {"sessions": 20, "annual_drift": -1.2, "annual_volatility": 0.4},
                    {"sessions": 40, "annual_drift": 0.4, "annual_volatility": 0.25}]}

    def files(self) -> set[str]:
        return {path.relative_to(self.root).as_posix() for path in self.root.rglob("*")
                if path.is_file() and ".quantarium" not in path.relative_to(self.root).parts}

    def wait(self, job: dict) -> dict:
        done = self.api.wait(job["id"])
        self.assertEqual(done["status"], "succeeded", done["error"])
        return done["result"]


if __name__ == "__main__":
    unittest.main()
