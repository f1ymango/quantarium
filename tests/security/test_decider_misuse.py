"""v0.6.4: deciders are the user's programs; agents can neither register, choose, nor start one."""

from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path

from quantarium.decisions import load_decisions

from .harness import CANARY_ENV, ROOT, MisuseCase

FAKE = ROOT / "tests" / "fake_decider.py"


class DeciderMisuseTests(MisuseCase):
    def setUp(self) -> None:
        super().setUp()
        shutil.copytree(ROOT / "examples" / "scenarios" / "steady_bull", self.root / "scenarios" / "steady_bull")
        self.api.command("decider-create", {"name": "mine", "command": [sys.executable, str(FAKE), "trend"]})
        record = self.api.command("cell-create", {"name": "User decider"})
        self.cell = self.api.command("cell-save", {"id": record["id"], "spec": {
            "mode": "simple", "symbols": ["AAA"], "initial_cash": 10000, "sleeves": [{"decider": "mine"}]}})

    def test_agents_cannot_register_read_choose_or_start_a_decider(self) -> None:
        refused = []
        status, _ = self.agent("command", "decider-create", {"name": "evil", "command": [sys.executable, "-c", "0"]})
        refused.append(("decider-create", status))
        refused.append(("decider-delete", self.agent("command", "decider-delete", {"name": "mine"})[0]))
        refused.append(("get deciders", self.agent("get", "deciders")[0]))
        status, own = self.agent("command", "cell-create", {"name": "Agent cell"})
        refused.append(("cell-save decider", self.agent("command", "cell-save", {"id": own["id"], "spec": {
            "mode": "simple", "symbols": ["AAA"], "sleeves": [{"decider": "mine"}]}})[0]))
        refused.append(("deploy user's decider cell", self.agent("job", "deploy-suite", {
            "cells": [self.cell["id"]], "scenarios": ["scenarios/steady_bull"]})[0]))
        # A config the user's own run generated still names the decider.
        job = self.api.wait(self.api.submit("deploy-suite", {"cells": [self.cell["id"]],
                                                              "scenarios": ["scenarios/steady_bull"]})["id"])
        refused.append(("run-suite generated config", self.agent("job", "run-suite", {
            "config": job["result"]["config"]})[0]))
        allowed = [name for name, status in refused if status < 400 or status >= 500]
        self.assertEqual(allowed, [], refused)
        self.assertEqual(self.api.get("deciders", {})[0]["name"], "mine")

    def test_a_config_cannot_hide_the_decider_behind_an_escape_or_the_old_name(self) -> None:
        job = self.api.wait(self.api.submit("deploy-suite", {"cells": [self.cell["id"]],
                                                              "scenarios": ["scenarios/steady_bull"]})["id"])
        text = (self.root / job["result"]["config"]).read_text(encoding="utf-8")
        self.assertIn("quantarium.referee:LiveDecider", text)
        spellings = {"escaped": text.replace("LiveDecider", "\\u004civeDecider"),
                     "old name": text.replace("quantarium.referee:LiveDecider", "barloom.referee:LiveDecider")}
        refused = []
        for label, spelled in spellings.items():
            key = f"configs/generated/{label.replace(' ', '_')}.json"
            (self.root / key).write_text(spelled, encoding="utf-8")
            refused.append((label, self.agent("job", "run-suite", {"config": key})[0]))
        self.assertEqual([label for label, status in refused if status != 403], [], refused)

    def test_no_canary_reaches_a_decider_unless_the_user_names_it(self) -> None:
        names = [name for name in CANARY_ENV]
        self.api.command("decider-create", {"name": "env", "command": [sys.executable, str(FAKE), "env", ",".join(names)],
                                            "env": ["QUANTARIUM_TEST_KEY"]})
        record = self.api.command("cell-create", {"name": "Env decider"})
        cell = self.api.command("cell-save", {"id": record["id"], "spec": {
            "mode": "simple", "symbols": ["AAA"], "initial_cash": 10000, "sleeves": [{"decider": "env"}]}})
        job = self.api.wait(self.api.submit("deploy-suite", {"cells": [cell["id"]],
                                                              "scenarios": ["scenarios/steady_bull"]})["id"])
        self.assertEqual(job["status"], "succeeded", job["error"])
        tape = next((self.root / "decisions" / "recorded").glob("env-*.jsonl"))
        notes = {entry.note for entry in load_decisions(tape).decisions.values()}
        self.assertEqual(notes, {"seen:QUANTARIUM_TEST_KEY"})
        # The named canary reached only the decider's own process; the tape holds names, not values.


if __name__ == "__main__":
    unittest.main()
