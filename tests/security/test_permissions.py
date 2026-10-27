"""v0.5.1: the agent checkpoint against a hostile agent: tiers, holdout spellings, and proposals."""

from __future__ import annotations

import json
import unittest

from quantarium import agents

from .harness import MisuseCase

HOLD = "class Hold:\n    def on_bar(self, context):\n        return 1.0\n"
# Planted in the holdout scenario's name: an agent that can see it has read the holdout.
MARKER = "HOLDOUTMARKER7f3a"


def scenario_spec(name: str, seed: int) -> dict:
    return {"name": name, "seed": seed, "start_date": "2030-01-07", "symbols": ["AAA"],
            "segments": [{"sessions": 40, "annual_drift": 0.2, "annual_volatility": 0.2}]}


class HoldoutMisuseTests(MisuseCase):
    def setUp(self) -> None:
        super().setUp()
        for folder, name, seed in (("visible", "visible", 1), ("secret", MARKER, 2)):
            job = self.api.submit("scenario-generate", {"output": f"scenarios/{folder}", "spec": scenario_spec(name, seed)})
            self.assertEqual(self.api.wait(job["id"])["status"], "succeeded")
        (self.root / "strategies").mkdir()
        (self.root / "strategies" / "hold.py").write_text(HOLD, encoding="utf-8")
        self.cell = self.api.command("cell-create", {"name": "Holder"})
        self.api.command("cell-save", {"id": self.cell["id"], "spec": {
            "mode": "simple", "symbols": ["AAA"], "initial_cash": 1000,
            "sleeves": [{"script": "strategies/hold.py", "class": "Hold"}]}})
        self.api.command("scenario-holdout", {"key": "scenarios/secret", "holdout": True})
        self.agent_replies: list[object] = []

    def spellings(self) -> list[str]:
        absolute = self.root / "scenarios" / "secret"
        return ["scenarios/secret", "scenarios/secret/", "scenarios/./secret", "scenarios/SECRET", "Scenarios/secret",
                "scenarios/secret.", "scenarios\\secret", "scenarios/visible/../secret", "scenarios//secret",
                str(absolute), str(absolute).replace("\\", "/"), "scenarios/secret/scenario.json",
                "scenarios/secret/prices.csv", "scenarios/SECRET/PRICES.CSV"]

    def agent_sees(self, kind: str, name: str, params: dict) -> tuple[int, object]:
        status, body = self.agent(kind, name, params)
        self.agent_replies.append(body)
        return status, body

    def assert_no_marker(self) -> None:
        self.assertNotIn(MARKER, json.dumps(self.agent_replies, default=str))

    def test_every_spelling_of_a_holdout_path_is_refused(self) -> None:
        allowed = []
        for spelling in self.spellings():
            requests = [("get", "scenario", {"key": spelling}),
                        ("job", "scenario-slice", {"source": spelling, "output": "scenarios/copy", "start": 0, "end": 10}),
                        ("job", "scenario-transform", {"source": spelling, "output": "scenarios/copy2", "spec": {}}),
                        ("job", "deploy-suite", {"cells": [self.cell["id"]], "scenarios": [spelling]}),
                        ("command", "proposal-create", {"action": "lab-register", "params": {
                            "idea": "x", "name": "x", "cells": [self.cell["id"]], "scenarios": [spelling]}})]
            for kind, name, params in requests:
                status, body = self.agent_sees(kind, name, params)
                if status < 400:
                    allowed.append(f"{name} {spelling}: {status} {str(body)[:120]}")
        self.wait_for_jobs()
        with self.api._lock:
            jobs = [dict(job) for job in self.api.jobs.values() if job.get("agent_connection")]
        succeeded = [f"{job['action']} {job['params']}" for job in jobs if job["status"] == "succeeded"]
        self.assertEqual(allowed, [])
        self.assertEqual(succeeded, [])
        self.assert_no_marker()

    def test_reads_never_show_the_holdout(self) -> None:
        for route in ("workspace", "scripts", "cells", "lab", "arena", "jobs"):
            self.agent_sees("get", route, {})
        self.assert_no_marker()


class TierTests(MisuseCase):
    """A connection holding only some tiers reaches nothing above them, however it asks."""

    def test_an_observer_cannot_change_anything(self) -> None:
        observer = self.api.agents.create_connection("Observer", tiers=["observe"])["token"]
        before = sorted(path.name for path in self.root.rglob("*") if ".quantarium" not in path.parts)
        attempts = [("command", name) for name, tier in agents.OPERATIONS["command"].items() if tier != "observe"]
        attempts += [("job", name) for name in agents.OPERATIONS["job"]]
        attempts += [("command", name) for name in ("cell-delete", "agents-disable", "agent-connection-create",
                                                    "cell-ask-grant", "script-review", "scenario-holdout",
                                                    "proposal-decide", "chat-send", "onboarding-start")]
        allowed = []
        for kind, name in attempts:
            endpoint, field = ("commands", "command") if kind == "command" else ("jobs", "action")
            for path in (f"/api/v1/{endpoint}", f"/api/v1/{endpoint}/", f"/api/v1//{endpoint}"):
                status, body = self.call("POST", path, {field: name, "params": {"name": "x"}}, token=observer)
                if status < 400 or status >= 500:
                    allowed.append(f"{path} {name}: {status} {str(body)[:100]}")
        self.assertEqual(allowed, [])
        self.assertEqual(sorted(path.name for path in self.root.rglob("*") if ".quantarium" not in path.parts), before)

    def test_browser_only_reads_are_closed_to_agents(self) -> None:
        for route in ("agents", "agents/log", "agents/scripts", "agents/proposals", "agents/chats", "onboarding",
                      "supercells", "tapes/x", "agents/chats/x"):
            status, _ = self.agent("get", route)
            self.assertIn(status, {403, 404}, route)

    def test_a_revoked_or_wrong_token_does_nothing(self) -> None:
        for token in ("", "x", self.token + "x", self.token.upper(), self.token[:-1]):
            status, _ = self.call("GET", "/api/v1/cells", token=token)
            self.assertGreaterEqual(status, 400, repr(token[:8]))
        connection = self.api.agents.connection_for(self.token)
        self.api.command("agent-connection-revoke", {"id": connection["id"]})
        self.assertEqual(self.agent("get", "cells")[0], 401)


if __name__ == "__main__":
    unittest.main()
