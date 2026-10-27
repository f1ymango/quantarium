"""v1.0.1: Quantarium reads everything Barloom wrote (quantarium/legacy.py)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from quantarium import agents
from quantarium.agent_log import SECRET_VALUE
from quantarium.agents import AgentSettings
from quantarium.decisions import parse_decisions
from quantarium.engine import run_static
from quantarium.evidence import verify
from quantarium.legacy import current_value, env
from quantarium.model import CellSpec, RunSpec, StrategySpec
from quantarium.runtimes import SECRET_PREFIXES
from quantarium.scenario import load_scenario
from quantarium.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]
STEADY = ROOT / "examples" / "scenarios" / "steady_bull"


class LegacyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_an_old_hidden_folder_is_renamed_once_and_its_contents_kept(self) -> None:
        (self.root / ".barloom" / "tapes").mkdir(parents=True)
        (self.root / ".barloom" / "holdout.json").write_text('{"keys": []}', encoding="utf-8")
        workspace = Workspace(self.root)
        self.assertFalse((self.root / ".barloom").exists())
        self.assertEqual((self.root / ".quantarium" / "holdout.json").read_text(encoding="utf-8"), '{"keys": []}')
        # A saved setting that still names the old folder resolves inside the new one.
        self.assertEqual(workspace.resolve(".barloom/tapes/c_1.jsonl"),
                         (self.root / ".quantarium" / "tapes" / "c_1.jsonl").resolve())

    def test_a_workspace_with_both_folders_keeps_the_new_one_and_leaves_the_old_alone(self) -> None:
        (self.root / ".barloom").mkdir()
        (self.root / ".barloom" / "old.txt").write_text("old", encoding="utf-8")
        (self.root / ".quantarium").mkdir()
        Workspace(self.root)
        self.assertTrue((self.root / ".barloom" / "old.txt").is_file())
        self.assertFalse((self.root / ".quantarium" / "old.txt").exists())

    def test_the_old_workspace_file_and_variables_are_read(self) -> None:
        (self.root / "barloom-workspace.json").write_text(json.dumps({"data": ["my_data"]}), encoding="utf-8")
        self.assertEqual(Workspace(self.root).folders["data"], ["my_data"])
        with mock.patch.dict(os.environ, {"BARLOOM_TOKEN": "old"}, clear=False):
            os.environ.pop("QUANTARIUM_TOKEN", None)
            self.assertEqual(env("TOKEN"), "old")
            with mock.patch.dict(os.environ, {"QUANTARIUM_TOKEN": "new"}):
                self.assertEqual(env("TOKEN"), "new")

    def test_old_formats_still_read(self) -> None:
        decisions = {"format": "barloom-decisions-v1", "source": {"system": "s"}, "symbols": ["AAA"],
                     "decisions": [{"date": "2030-01-07", "weights": {"AAA": 1.0}}]}
        self.assertEqual(len(parse_decisions(json.dumps(decisions).encode()).decisions), 1)
        scenario = self.root / "old_scenario"
        shutil.copytree(STEADY, scenario)
        manifest = json.loads((scenario / "scenario.json").read_text(encoding="utf-8"))
        manifest["format"] = "barloom-scenario-v1"
        (scenario / "scenario.json").write_text(json.dumps(manifest), encoding="utf-8")
        self.assertEqual(load_scenario(scenario).name, "steady_bull")
        embedded = json.dumps({"format": "barloom-evidence-report-v1", "record": {"id": "x"}},
                              sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        digest = hashlib.sha256(embedded.encode()).hexdigest()
        page = f'<script type="application/json" id="barloom-record" data-sha256="{digest}">{embedded}</script>'
        self.assertTrue(verify(page)["internally_consistent"])

    def test_old_entrypoints_and_imports_run(self) -> None:
        import barloom.model
        import quantarium.model

        self.assertIs(barloom.model.Decision, quantarium.model.Decision)
        cell = CellSpec("old", "complex", ("AAA", "BBB"), 10_000,
                        (StrategySpec("barloom.lab_controls:EqualWeight", 1, {}),))
        run_id = run_static(RunSpec(str(STEADY / "prices.csv"), str(self.root / "r.sqlite"), 0, 0, (cell,)),
                            "{}", ROOT / "examples")
        self.assertTrue(run_id)
        self.assertEqual(current_value({"entrypoint": "barloom.referee:ReplayInbox",
                                        "parameters": {"inbox": "C:/w/.barloom/inboxes/a.jsonl"}}),
                         {"entrypoint": "quantarium.referee:ReplayInbox",
                          "parameters": {"inbox": "C:/w/.quantarium/inboxes/a.jsonl"}})
        # A strategy's own parameters that merely start with the old name are left alone.
        for text in ("barloom.csv", "barloom.notes", "barloom.x:y z", "my.barloom/file"):
            self.assertEqual(current_value({"file": text}), {"file": text})

    def test_an_old_token_still_opens_its_connection(self) -> None:
        settings = AgentSettings(Workspace(self.root))
        settings.enable(agents.CONFIRMATION, agents.NOTICE_VERSION)
        connection = settings.create_connection("Old agent", tiers=["observe"])
        old = "blm_" + connection["token"][4:]
        path = self.root / ".quantarium" / "agents.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        for item in data["connections"]:
            item["token_sha256"] = hashlib.sha256(old.encode()).hexdigest()
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(settings.connection_for(old)["name"], "Old agent")

    def test_old_tokens_and_variables_stay_secret(self) -> None:
        self.assertTrue(SECRET_VALUE.search("token blm_abcdefghijkl here"))
        self.assertTrue(SECRET_VALUE.search("token qtm_abcdefghijkl here"))
        self.assertIn("BARLOOM_", SECRET_PREFIXES)
        self.assertIn("QUANTARIUM_", SECRET_PREFIXES)


if __name__ == "__main__":
    unittest.main()
