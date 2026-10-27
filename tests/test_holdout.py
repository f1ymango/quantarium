"""Holdout scenarios stay out of agents' sight, and variants are counted."""

from __future__ import annotations

import http.client
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from quantarium import agents
from quantarium.server import TOKEN_HEADER, QuantariumServer
from quantarium.workspace import Workspace


ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)
HOLD = "class Hold:\n    def on_bar(self, context):\n        return 1.0\n"


def spec(name: str, seed: int) -> dict:
    return {"name": name, "seed": seed, "start_date": "2030-01-07", "symbols": ["AAA"],
            "segments": [{"sessions": 40, "annual_drift": 0.2, "annual_volatility": 0.2}]}


class HoldoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.root = Path(self.temp.name)
        self.server = QuantariumServer(Workspace(self.root), port=0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.api = self.server.api
        for name, seed in (("visible", 1), ("secret", 2)):
            self.wait(self.api.submit("scenario-generate", {"output": f"scenarios/{name}", "spec": spec(name, seed)}))
        (self.root / "strategies").mkdir()
        (self.root / "strategies" / "hold.py").write_bytes(HOLD.encode())
        self.cell = self.api.command("cell-create", {"name": "Holder"})
        self.api.command("cell-save", {"id": self.cell["id"], "spec": self.cell_spec(1000)})
        self.command("agents-enable", {"confirmation": agents.CONFIRMATION, "notice_version": agents.NOTICE_VERSION})
        status, body = self.command("agent-connection-create", {"name": "Researcher"})
        self.token = body["token"]
        self.assertEqual(self.command("scenario-holdout", {"key": "scenarios/secret", "holdout": True})[0], 200)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    @staticmethod
    def cell_spec(cash: int) -> dict:
        return {"mode": "simple", "symbols": ["AAA"], "initial_cash": cash,
                "sleeves": [{"script": "strategies/hold.py", "class": "Hold"}]}

    def wait(self, job: dict) -> dict:
        done = self.api.wait(job["id"])
        self.assertEqual(done["status"], "succeeded", done["error"])
        return done["result"]

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

    def job(self, action: str, params: dict, token: str | None = None):
        return self.call("POST", "/api/v1/jobs", {"action": action, "params": params}, token)

    def assertHoldout(self, response) -> None:
        status, body = response
        self.assertEqual((status, body.get("code")), (403, "holdout"), body)

    def test_agents_cannot_see_read_or_run_on_holdout_scenarios(self) -> None:
        browser = [item["name"] for item in self.call("GET", "/api/v1/workspace")[1]["scenarios"] if item["holdout"]]
        self.assertEqual(browser, ["secret"])
        agent = [item["name"] for item in self.call("GET", "/api/v1/workspace", token=self.token)[1]["scenarios"]]
        self.assertEqual(agent, ["visible"])
        self.assertHoldout(self.call("GET", "/api/v1/scenario?key=scenarios/secret", token=self.token))
        self.assertHoldout(self.job("deploy-suite", {"cells": [self.cell["id"]], "scenarios": ["scenarios/secret"]},
                                    self.token))
        self.assertHoldout(self.job("scenario-transform", {"source": "scenarios/secret", "output": "scenarios/leak",
                                                           "spec": {"name": "leak"}}, self.token))
        self.assertHoldout(self.job("deploy-static", {"cells": [self.cell["id"]], "data": "scenarios/secret/prices.csv"},
                                    self.token))
        # Only the user marks scenarios as holdout.
        status, body = self.command("scenario-holdout", {"key": "scenarios/secret", "holdout": False}, self.token)
        self.assertEqual((status, body["code"]), (403, "not_permitted"))

    def test_results_on_holdout_data_stay_hidden_even_from_a_copy(self) -> None:
        # The user tests the cell on the holdout scenario.
        result = self.wait(self.api.submit("deploy-suite", {"cells": [self.cell["id"]], "scenarios": ["scenarios/secret"]}))
        suite = result["config"]
        card = next(item for item in self.call("GET", "/api/v1/cells")[1] if item["id"] == self.cell["id"])
        self.assertIsNotNone(card["result"])
        card = next(item for item in self.call("GET", "/api/v1/cells", token=self.token)[1] if item["id"] == self.cell["id"])
        self.assertIsNone(card["result"])
        detail = self.call("GET", f"/api/v1/cells/{self.cell['id']}", token=self.token)[1]
        self.assertEqual((detail["deployments"], detail["results"]), ([], []))
        self.assertHoldout(self.call("GET", f"/api/v1/suite-report?config={suite}&summary=1", token=self.token))
        run_id = self.call("GET", f"/api/v1/suite-report?config={suite}")[1]["matrix"][0]["run_id"]
        self.assertHoldout(self.call("GET", f"/api/v1/analysis?db=runs/suites.sqlite&kind=static&id={run_id}",
                                     token=self.token))
        self.assertHoldout(self.call("GET", "/api/v1/analysis?db=runs/suites.sqlite&kind=static", token=self.token))

        # A copy under another name has the same content, so its results stay hidden too.
        shutil.copytree(self.root / "scenarios" / "secret", self.root / "scenarios" / "copy")
        status, job = self.job("deploy-suite", {"cells": [self.cell["id"]], "scenarios": ["scenarios/copy"]}, self.token)
        self.assertEqual(status, 202, job)
        done = self.api.wait(job["id"])
        self.assertHoldout(self.call("GET", f"/api/v1/suite-report?config={done['result']['config']}&summary=1",
                                     token=self.token))

    def test_one_holdout_test_per_cell_version_through_a_proposal(self) -> None:
        status, proposal = self.command("proposal-create", {"action": "holdout-test", "params": {
            "cells": [self.cell["id"]]}, "rationale": "Passed every visible scenario"}, self.token)
        self.assertEqual(status, 200, proposal)
        status, decided = self.command("proposal-decide", {"id": proposal["id"], "decision": "approve"})
        self.assertEqual(status, 200, decided)
        done = self.api.wait(decided["job_id"])
        self.assertEqual(done["status"], "succeeded", done["error"])
        report = self.call("GET", f"/api/v1/suite-report?config={done['result']['config']}")[1]
        self.assertEqual([row["scenario"] for row in report["matrix"]], ["secret"])
        self.assertHoldout(self.call("GET", f"/api/v1/suite-report?config={done['result']['config']}", token=self.token))
        status, body = self.command("proposal-create", {"action": "holdout-test", "params": {
            "cells": [self.cell["id"]]}, "rationale": "Again"}, self.token)
        self.assertEqual((status, body["code"]), (400, "holdout_used"))
        # A changed cell is a new variant, so it may be tested once.
        self.command("cell-save", {"id": self.cell["id"], "spec": self.cell_spec(2000)}, self.token)
        status, body = self.command("proposal-create", {"action": "holdout-test", "params": {
            "cells": [self.cell["id"]]}, "rationale": "Changed"}, self.token)
        self.assertEqual(status, 200, body)
        # A feed test on the holdout scenario cannot even be proposed.
        status, body = self.command("proposal-create", {"action": "deploy-feed", "params": {
            "cells": [self.cell["id"]], "feed_id": "peek", "provider": "scenario", "source": "scenarios/secret",
            "start_date": "2030-01-07"}, "rationale": "Peek"}, self.token)
        self.assertEqual((status, body["code"]), (403, "holdout"))

    def test_variants_are_counted_and_reported(self) -> None:
        for cash in (1000, 2000, 2000, 3000):
            self.command("cell-save", {"id": self.cell["id"], "spec": self.cell_spec(cash)}, self.token)
            status, job = self.job("deploy-suite", {"cells": [self.cell["id"]], "scenarios": ["scenarios/visible"]},
                                   self.token)
            done = self.api.wait(job["id"])
        summary = self.call("GET", f"/api/v1/suite-report?config={done['result']['config']}&summary=1",
                            token=self.token)[1]
        self.assertEqual(summary["variants_tested"], 3)
        status, proposal = self.command("proposal-create", {"action": "cell-archive", "params": {
            "ids": [self.cell["id"]]}, "rationale": "Done with it"}, self.token)
        self.assertEqual(proposal["variants_tested"], 3)
        self.assertNotIn("variants_tested", self.call("GET", f"/api/v1/suite-report?config={done['result']['config']}&summary=1")[1])


if __name__ == "__main__":
    unittest.main()
