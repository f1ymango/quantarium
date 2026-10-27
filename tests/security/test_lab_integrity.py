"""v0.5.3: the Lab ledger cannot be made to flatter a candidate by renaming or copying used data."""

from __future__ import annotations

import shutil
import unittest

from .harness import ROOT, MisuseCase


def spec(name: str) -> dict:
    return {"name": name, "seed": 11, "start_date": "2030-01-07", "symbols": ["AAA"],
            "segments": [{"sessions": 80, "annual_drift": 0.15, "annual_volatility": 0.2}]}


class LabIntegrityTests(MisuseCase):
    def setUp(self) -> None:
        super().setUp()
        (self.root / "strategies").mkdir()
        shutil.copy(ROOT / "examples/strategies.py", self.root / "strategies/strategies.py")
        record = self.api.command("cell-create", {"name": "Candidate"})
        self.cell = self.api.command("cell-save", {"id": record["id"], "spec": {
            "mode": "simple", "symbols": ["AAA"], "initial_cash": 10000,
            "sleeves": [{"script": "strategies/strategies.py", "class": "TrendFollower",
                         "allocation": 1, "parameters": {"window": 10}}]}})

    def generate(self, folder: str, name: str) -> None:
        job = self.api.submit("scenario-generate", {"output": f"scenarios/{folder}", "spec": spec(name)})
        self.assertEqual(self.api.wait(job["id"])["status"], "succeeded")

    def register_and_run(self, scenario: str) -> dict:
        registered = self.api.command("lab-register", {
            "idea": "trend", "name": "Trend vs controls", "cells": [self.cell["id"]], "scenarios": [scenario],
            "pass_rule": {"min_mean_excess_return_pp": 0, "min_sessions": 60}})
        job = self.api.wait(self.api.submit("lab-run", {"id": registered["id"]})["id"])
        self.assertEqual(job["status"], "succeeded", job["error"])
        return job["result"]

    def test_the_same_prices_under_a_new_name_count_as_used(self) -> None:
        self.generate("first", "first")
        self.generate("again", "again")
        first = (self.root / "scenarios/first/prices.csv").read_bytes()
        self.assertEqual(first, (self.root / "scenarios/again/prices.csv").read_bytes())
        self.register_and_run("scenarios/first")
        later = self.register_and_run("scenarios/again")
        self.assertTrue(later["reused_families"], "identical data under a new name was treated as fresh")
        self.assertTrue(later["result"]["verdict"]["used_data"])

    def test_a_copied_folder_counts_as_used(self) -> None:
        self.generate("first", "first")
        shutil.copytree(self.root / "scenarios/first", self.root / "scenarios/copy")
        self.register_and_run("scenarios/first")
        later = self.register_and_run("scenarios/copy")
        self.assertTrue(later["reused_families"])


if __name__ == "__main__":
    unittest.main()
