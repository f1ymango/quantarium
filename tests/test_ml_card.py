"""Model cards are data-only, bounded by label dates, and visible in journals."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

from quantarium.api import Api
from quantarium.config import load_live_config
from quantarium.ml.card import FEATURES, load_card, predict, save_card
from quantarium.ml.train_risk import _bars, examples
from quantarium.workspace import Workspace


ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)


def example_card(boundary: str) -> dict:
    return {"schema": 1, "kind": "drop_risk_logistic", "created_utc": "2029-12-31T00:00:00+00:00",
            "features": list(FEATURES),
            "recipe": {"decision_time": "daily_close", "entry_time": "next_open",
                       "horizon": 5, "drop": 0.05},
            "data_manifest": {"source": "test fixture", "source_status": "user_declared",
                              "sha256": "0" * 64},
            "information_end": boundary,
            "weights": {"mean": [0, 0], "scale": [1, 1], "coef": [0, 0],
                        "intercept": 0}, "training": {"algorithm": "test fixture"}}


class ModelCardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_card_is_data_only_and_content_addressed(self) -> None:
        path = save_card(self.root / "models", example_card("2029-12-31"))
        card = load_card(path)
        self.assertEqual(card["information_end"], "2029-12-31")
        self.assertAlmostEqual(predict(card, (0, 0)), 0.5)
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "content does not match"):
            load_card(path)

    def test_training_excludes_labels_ending_after_through(self) -> None:
        data = _bars(ROOT / "examples/scenarios/bull_with_crash/prices.csv")
        values, labels, ends, decisions = examples(data, date(2030, 8, 1), 5, 0.05)
        self.assertTrue(values)
        self.assertEqual(len(values), len(labels))
        self.assertLessEqual(max(ends), date(2030, 8, 1))
        self.assertLess(max(decisions), max(ends))

    def test_daily_card_is_rejected_for_minute_feed(self) -> None:
        source = json.loads((ROOT / "examples/live_experiment.json").read_text(encoding="utf-8"))
        source["cells"][0]["model_card"] = "models/risk_placeholder.json"
        config = self.root / "live.json"
        config.write_text(json.dumps(source), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Daily model cards"):
            load_live_config(config)

    def test_cli_trains_in_a_child_process_and_writes_a_card(self) -> None:
        completed = subprocess.run([
            sys.executable, "-m", "quantarium", "ml", "train-risk",
            str(ROOT / "examples/scenarios/bull_with_crash/prices.csv"),
            "--output-dir", str(self.root / "models"), "--through", "2030-12-31",
            "--source", "example scenario",
        ], cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        card = load_card(Path(result["model_card"]))
        self.assertEqual(card["data_manifest"]["source_status"], "user_declared")
        self.assertEqual(result["information_end"], card["information_end"])
        self.assertLessEqual(date.fromisoformat(result["information_end"]), date(2030, 12, 31))
        self.assertGreater(result["training_rows"], 40)

    def test_a_cell_records_point_in_time_estimates(self) -> None:
        (self.root / "strategies").mkdir()
        (self.root / "data").mkdir()
        shutil.copy(ROOT / "examples/strategies.py", self.root / "strategies/strategies.py")
        shutil.copy(ROOT / "examples/scenarios/steady_bull/prices.csv", self.root / "data/prices.csv")
        path = save_card(self.root / "models", example_card("2029-12-31"))
        api = Api(Workspace(self.root))
        try:
            record = api.command("cell-create", {"name": "Model observer"})
            api.command("cell-save", {"id": record["id"], "spec": {
                "mode": "simple", "symbols": ["AAA"], "initial_cash": 10000,
                "model_card": "models/" + path.name,
                "sleeves": [{"script": "strategies/strategies.py", "class": "TrendFollower",
                             "allocation": 1, "parameters": {"window": 10}}],
            }})
            card, = api.get("cells", {})
            self.assertEqual(card["model_card"], "models/" + path.name)
            job = api.wait(api.submit("deploy-static", {"cells": [record["id"]],
                                                         "data": "data/prices.csv"})["id"])
            self.assertEqual(job["status"], "succeeded", job["error"])
            run = job["result"]
            timeline = api.get("timeline", {"db": run["db"], "kind": "static",
                                            "id": run["id"], "cell": "Model observer"})
            events = [event for day in timeline["days"] for event in day["other_events"]
                      if event["kind"] == "model_estimate"]
            self.assertTrue(events)
            self.assertAlmostEqual(events[0]["payload"]["risk_of_drop"]["AAA"], 0.5)
            self.assertTrue(events[0]["payload"]["post_information_end"])
        finally:
            api.close()

    def _lab_with_boundary(self, boundary: str) -> dict:
        (self.root / "strategies").mkdir()
        shutil.copy(ROOT / "examples/strategies.py", self.root / "strategies/strategies.py")
        shutil.copytree(ROOT / "examples/scenarios/steady_bull", self.root / "scenarios/steady_bull")
        path = save_card(self.root / "models", example_card(boundary))
        api = Api(Workspace(self.root))
        try:
            record = api.command("cell-create", {"name": "Risk observer"})
            api.command("cell-save", {"id": record["id"], "spec": {
                "mode": "simple", "symbols": ["AAA"], "initial_cash": 10000,
                "model_card": "models/" + path.name,
                "sleeves": [{"script": "strategies/strategies.py", "class": "TrendFollower",
                             "allocation": 1, "parameters": {"window": 10}}],
            }})
            registered = api.command("lab-register", {
                "idea": "risk_observer", "name": "Risk observer vs controls",
                "cells": [record["id"]], "scenarios": ["scenarios/steady_bull"],
                "pass_rule": {"min_mean_excess_return_pp": 0, "min_sessions": 60},
            })
            self.assertEqual(registered["spec"]["model_card_sha256"], load_card(path)["sha256"])
            job = api.wait(api.submit("lab-run", {"id": registered["id"]})["id"])
            self.assertEqual(job["status"], "succeeded", job["error"])
            return job["result"]["result"]["verdict"]
        finally:
            api.close()

    def test_bound_card_allows_a_scoped_verdict_after_its_boundary(self) -> None:
        verdict = self._lab_with_boundary("2029-12-31")
        self.assertIn(verdict["status"], {"supported_within_tested_scope", "not_supported"})
        self.assertEqual(verdict["scope"]["information_boundary_status"], "model_card_bound")

    def test_overlapping_training_boundary_cannot_earn_a_verdict(self) -> None:
        verdict = self._lab_with_boundary("2030-03-01")
        self.assertEqual(verdict["status"], "unknown")
        self.assertTrue(any("final training label" in caveat for caveat in verdict["caveats"]))


if __name__ == "__main__":
    unittest.main()
