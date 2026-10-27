"""Proposals: agents ask, only the user approves, and nothing changes in between."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from quantarium import agents
from quantarium.provenance import REGISTRY
from quantarium.server import TOKEN_HEADER, QuantariumServer
from quantarium.workspace import Workspace


ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)
HOLD = "class Hold:\n    def on_bar(self, context):\n        return 1.0\n"
SPEC = {"name": "m", "seed": 3, "start_date": "2030-01-07", "symbols": ["AAA"],
        "segments": [{"sessions": 40, "annual_drift": 0.1, "annual_volatility": 0.2}]}


class ProposalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.root = Path(self.temp.name)
        self.server = QuantariumServer(Workspace(self.root), port=0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.command("agents-enable", {"confirmation": agents.CONFIRMATION, "notice_version": agents.NOTICE_VERSION})
        self.token = self.connect("Proposer")
        api = self.server.api
        api.wait(api.submit("scenario-generate", {"output": "scenarios/m", "spec": SPEC})["id"])
        self.command("script-write", {"key": "strategies/agent/hold.py", "source": HOLD}, self.token)
        _, self.cell = self.command("cell-create", {"name": "Idea"}, self.token)
        self.command("cell-save", {"id": self.cell["id"], "spec": {
            "mode": "simple", "symbols": ["AAA"], "initial_cash": 1000,
            "sleeves": [{"script": "strategies/agent/hold.py", "class": "Hold"}]}}, self.token)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def call(self, method: str, path: str, body: object = None, token: str | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        headers = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
            if token is None:
                headers[TOKEN_HEADER] = self.server.token
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        connection.request(method, path, body=None if body is None else json.dumps(body).encode(), headers=headers)
        response = connection.getresponse()
        data = json.loads(response.read())
        connection.close()
        return response.status, data

    def command(self, name: str, params: dict, token: str | None = None):
        return self.call("POST", "/api/v1/commands", {"command": name, "params": params}, token)

    def connect(self, name: str, **options) -> str:
        status, body = self.command("agent-connection-create", {"name": name, **options})
        self.assertEqual(status, 200, body)
        return body["token"]

    def propose(self, action: str, params: dict, token: str | None = None, rationale: str = "It held up in tests"):
        return self.command("proposal-create", {"action": action, "params": params, "rationale": rationale,
                                                "evidence": ["job 123"]}, token or self.token)

    def feed(self, feed_id: str = "agent_forward") -> dict:
        return {"cells": [self.cell["id"]], "feed_id": feed_id, "provider": "scenario",
                "source": "scenarios/m", "start_date": "2030-01-07"}

    def test_a_feed_proposal_runs_only_after_the_user_approves(self) -> None:
        status, proposal = self.propose("deploy-feed", self.feed())
        self.assertEqual((status, proposal["status"]), (200, "pending"))
        self.assertEqual(list(proposal["pins"]["scripts"]), ["strategies/agent/hold.py"])
        self.assertEqual(self.call("GET", "/api/v1/arena")[1], [])
        # The agent cannot approve its own proposal, nor run Feed directly.
        status, body = self.command("proposal-decide", {"id": proposal["id"], "decision": "approve"}, self.token)
        self.assertEqual((status, body["code"]), (403, "not_permitted"))
        status, body = self.call("POST", "/api/v1/jobs", {"action": "deploy-feed", "params": self.feed()}, self.token)
        self.assertEqual((status, body["code"]), (403, "not_permitted"))

        _, inbox = self.call("GET", "/api/v1/agents/proposals?status=pending")
        self.assertEqual(inbox[0]["scripts"], [{"key": "strategies/agent/hold.py",
                                                "sha256": proposal["pins"]["scripts"]["strategies/agent/hold.py"],
                                                "agent_unreviewed": True}])
        status, decided = self.command("proposal-decide", {"id": proposal["id"], "decision": "approve"})
        self.assertEqual((status, decided["status"]), (200, "approved"), decided)
        done = self.server.api.wait(decided["job_id"])
        self.assertEqual(done["status"], "succeeded", done["error"])
        self.assertEqual([item["id"] for item in self.call("GET", "/api/v1/arena")[1]], ["agent_forward"])
        registry = json.loads((self.root / REGISTRY).read_text(encoding="utf-8"))
        self.assertEqual({entry["state"] for entry in registry.values()}, {"reviewed"})
        status, body = self.command("proposal-decide", {"id": proposal["id"], "decision": "approve"})
        self.assertEqual((status, body["code"]), (400, "proposal_decided"))

    def test_a_change_after_proposing_makes_it_stale(self) -> None:
        _, proposal = self.propose("cell-archive", {"ids": [self.cell["id"]]})
        self.command("cell-save", {"id": self.cell["id"], "name": "Idea, changed"}, self.token)
        status, body = self.command("proposal-decide", {"id": proposal["id"], "decision": "approve"})
        self.assertEqual((status, body["code"]), (400, "proposal_stale"))
        self.assertEqual(self.call("GET", "/api/v1/proposals", token=self.token)[1][0]["status"], "stale")
        self.assertFalse(self.call("GET", f"/api/v1/cells/{self.cell['id']}")[1]["archived"])

        # A script edit after proposing is a change too.
        _, proposal = self.propose("deploy-feed", self.feed())
        read = self.call("GET", "/api/v1/scripts/strategies/agent/hold.py")[1]
        self.command("script-write", {"key": "strategies/agent/hold.py", "source": HOLD + "# swapped\n",
                                      "base_sha256": read["sha256"]}, self.token)
        status, body = self.command("proposal-decide", {"id": proposal["id"], "decision": "approve"})
        self.assertEqual((status, body["code"]), (400, "proposal_stale"))
        self.assertEqual(self.call("GET", "/api/v1/arena")[1], [])

    def test_lab_registration_requires_user_approval_and_pins_scenario_data(self) -> None:
        params = {"idea": "controlled_idea", "name": "One candidate",
                  "cells": [self.cell["id"]], "scenarios": ["scenarios/m"],
                  "pass_rule": {"min_mean_excess_return_pp": 0, "min_sessions": 20}}
        status, proposed = self.propose("lab-register", params)
        self.assertEqual((status, proposed["status"]), (200, "pending"))
        self.assertIn("scenarios/m", proposed["pins"]["scenarios"])
        self.assertEqual(self.call("GET", "/api/v1/lab")[1], [])
        status, refused = self.command("proposal-decide", {"id": proposed["id"],
                                                           "decision": "approve"}, self.token)
        self.assertEqual((status, refused["code"]), (403, "not_permitted"))
        status, approved = self.command("proposal-decide", {"id": proposed["id"],
                                                            "decision": "approve"})
        self.assertEqual((status, approved["status"]), (200, "approved"))
        self.assertEqual(self.call("GET", "/api/v1/lab")[1][0]["status"], "registered")

        status, later = self.propose("lab-register", dict(params, idea="second_idea"))
        self.assertEqual(status, 200)
        prices = self.root / "scenarios/m/prices.csv"
        prices.write_text(prices.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        status, stale = self.command("proposal-decide", {"id": later["id"],
                                                         "decision": "approve"})
        self.assertEqual((status, stale["code"]), (400, "proposal_stale"))
        self.assertEqual(len(self.call("GET", "/api/v1/lab")[1]), 1)

    def test_lab_ledger_does_not_expose_a_later_holdout_to_agents(self) -> None:
        params = {"idea": "controlled_idea", "name": "One candidate",
                  "cells": [self.cell["id"]], "scenarios": ["scenarios/m"],
                  "pass_rule": {"min_sessions": 20}}
        status, record = self.command("lab-register", params)
        self.assertEqual(status, 200)
        self.assertEqual(len(self.call("GET", "/api/v1/lab", token=self.token)[1]), 1)
        self.command("scenario-holdout", {"key": "scenarios/m", "holdout": True})
        self.assertEqual(self.call("GET", "/api/v1/lab", token=self.token)[1], [])
        status, denied = self.call("GET", f"/api/v1/lab/{record['id']}", token=self.token)
        self.assertEqual((status, denied["code"]), (403, "holdout"))
        self.assertEqual(len(self.call("GET", "/api/v1/lab")[1]), 1)

    def test_rejections_carry_a_note_back_to_the_agent(self) -> None:
        _, proposal = self.propose("cell-archive", {"ids": [self.cell["id"]]})
        status, decided = self.command("proposal-decide", {"id": proposal["id"], "decision": "reject",
                                                           "note": "Keep it for now"})
        self.assertEqual((status, decided["status"]), (200, "rejected"))
        mine = self.call("GET", "/api/v1/proposals", token=self.token)[1]
        self.assertEqual((mine[0]["status"], mine[0]["note"]), ("rejected", "Keep it for now"))

    def test_proposals_are_checked_when_filed(self) -> None:
        cases = [
            (("cell-delete", {"ids": [self.cell["id"]]}), 400, "invalid_parameter"),
            (("deploy-feed", {**self.feed(), "cells": ["c_0000000000"]}), 404, "cell_not_found"),
            (("deploy-feed", {**self.feed(), "source": ".quantarium/x"}), 403, "not_permitted"),
            (("cell-archive", {"ids": []}), 400, "invalid_parameter"),
        ]
        for (action, params), status, code in cases:
            with self.subTest(action=action, params=params):
                got, body = self.propose(action, params)
                self.assertEqual((got, body["code"]), (status, code))
        got, body = self.propose("cell-archive", {"ids": [self.cell["id"]]}, rationale=" ")
        self.assertEqual(body["code"], "invalid_parameter")
        # Only agents file proposals, and only with the propose tier.
        status, body = self.command("proposal-create", {"action": "cell-archive", "params": {"ids": [self.cell["id"]]},
                                                        "rationale": "x"})
        self.assertEqual((status, body["code"]), (403, "not_permitted"))
        reader = self.connect("Reader", tiers=["observe"])
        status, body = self.propose("cell-archive", {"ids": [self.cell["id"]]}, token=reader)
        self.assertEqual((status, body["code"]), (403, "not_permitted"))
        self.assertEqual(self.call("GET", "/api/v1/agents/proposals")[1], [])

    def test_agents_see_only_their_own_and_budgets_apply(self) -> None:
        other = self.connect("Other", budgets={"proposals_per_day": 1})
        self.propose("cell-archive", {"ids": [self.cell["id"]]})
        self.propose("cell-archive", {"ids": [self.cell["id"]]}, token=other)
        status, body = self.propose("cell-archive", {"ids": [self.cell["id"]]}, token=other)
        self.assertEqual((status, body["code"]), (429, "budget_exceeded"))
        self.assertEqual([item["connection_name"] for item in self.call("GET", "/api/v1/proposals", token=other)[1]],
                         ["Other"])
        self.assertEqual(len(self.call("GET", "/api/v1/agents/proposals")[1]), 2)


if __name__ == "__main__":
    unittest.main()
