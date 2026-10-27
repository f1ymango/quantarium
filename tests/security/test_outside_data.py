"""v0.9.5: only the user brings outside data in.

Importing research data records a survivorship declaration, and turning a
trades file into decisions describes the user's own account: both are the
user's word. Agents may read the scenarios that result, but cannot create
them, whatever tiers they hold.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .harness import MisuseCase

ROOT = Path(__file__).resolve().parents[2]


class OutsideDataTests(MisuseCase):
    def setUp(self) -> None:
        super().setUp()
        shutil.copytree(ROOT / "examples" / "data" / "fictional_index", self.root / "data" / "fic")
        (self.root / "data" / "trades.csv").write_text(
            "date,symbol,side,shares,price\n2024-03-05,AAA,buy,10,100\n", encoding="utf-8")

    def test_agents_cannot_check_import_or_convert_outside_data(self) -> None:
        requests = [("command", "data-check", {"folder": "data/fic"}),
                    ("job", "data-import", {"folder": "data/fic", "name": "agent_copy"}),
                    ("command", "trades-convert", {"trades": "data/trades.csv"}),
                    ("command", "proposal-create", {"action": "data-import",
                                                    "params": {"folder": "data/fic", "name": "x"},
                                                    "rationale": "r"})]
        allowed = []
        for kind, name, params in requests:
            status, body = self.agent(kind, name, params)
            if status < 400:
                allowed.append(f"{name}: {status} {str(body)[:120]}")
        self.wait_for_jobs()
        self.assertEqual(allowed, [])
        self.assertFalse((self.root / "scenarios" / "agent_copy").exists())
        self.assertFalse((self.root / "decisions" / "trades.json").exists())

    def test_the_user_can_and_the_result_is_readable_by_agents(self) -> None:
        status, body = self.call("POST", "/api/v1/commands", {"command": "data-check", "params": {"folder": "data/fic"}})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["survivorship"], "survivorship_free")
        status, body = self.call("POST", "/api/v1/jobs", {"action": "data-import",
                                                         "params": {"folder": "data/fic", "name": "fic"}})
        self.assertLess(status, 400, body)
        self.wait_for_jobs()
        status, body = self.agent("get", "scenario", {"key": "scenarios/fic"})
        self.assertEqual(status, 200, body)
        self.assertIsNone(body["closes"]["DLTA"][0][1])  # not listed yet on the first day
        status, body = self.call("POST", "/api/v1/commands", {"command": "trades-convert",
                                                             "params": {"trades": "data/trades.csv"}})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["output"], "decisions/trades.json")
        # Converting again does not replace a decision file that a Lab registration may have pinned.
        status, body = self.call("POST", "/api/v1/commands", {"command": "trades-convert",
                                                             "params": {"trades": "data/trades.csv"}})
        self.assertEqual((status, body.get("code")), (409, "already_exists"), body)

    def test_paths_outside_the_workspace_are_refused_for_the_user_too(self) -> None:
        for params in ({"folder": "../outside"}, {"folder": str(self.outside)}):
            status, _ = self.call("POST", "/api/v1/commands", {"command": "data-check", "params": params})
            self.assertGreaterEqual(status, 400)
        status, _ = self.call("POST", "/api/v1/commands", {"command": "trades-convert",
                                                          "params": {"trades": "data/trades.csv",
                                                                     "output": "../outside/x.json"}})
        self.assertGreaterEqual(status, 400)
        self.assertFalse((self.outside / "x.json").exists())
