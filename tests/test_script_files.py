"""Reading, writing, and checking scripts, and the rule that agent-written
scripts never run until reviewed."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from quantarium import agents
from quantarium.provenance import REGISTRY
from quantarium.script_files import check_source
from quantarium.server import TOKEN_HEADER, QuantariumServer
from quantarium.workspace import Workspace


ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)
HOLD = "class Hold:\n    def on_bar(self, context):\n        return 1.0\n"
SPEC = {"name": "m", "seed": 3, "start_date": "2030-01-07", "symbols": ["AAA"],
        "segments": [{"sessions": 30, "annual_drift": 0.1, "annual_volatility": 0.2}]}


class ScriptFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.root = Path(self.temp.name)
        self.server = QuantariumServer(Workspace(self.root), port=0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def call(self, method: str, path: str, body: object = None, token: str | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        headers = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
            if token is None:
                headers[TOKEN_HEADER] = self.server.token
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        connection.request(method, path, body=None if body is None else json.dumps(body).encode(),
                           headers=headers)
        response = connection.getresponse()
        data = json.loads(response.read())
        connection.close()
        return response.status, data

    def command(self, name: str, params: dict, token: str | None = None):
        return self.call("POST", "/api/v1/commands", {"command": name, "params": params}, token)

    def agent_token(self, **options) -> str:
        self.command("agents-enable", {"confirmation": agents.CONFIRMATION,
                                       "notice_version": agents.NOTICE_VERSION})
        status, body = self.command("agent-connection-create", {"name": options.pop("name", "Bot"), **options})
        self.assertEqual(status, 200, body)
        return body["token"]

    def test_read_write_and_check_with_base_hash(self) -> None:
        status, written = self.command("script-write", {"key": "strategies/mine.py", "source": HOLD})
        self.assertEqual(status, 200, written)
        self.assertTrue(written["check"]["ok"])
        status, read = self.call("GET", "/api/v1/scripts/strategies/mine.py")
        self.assertEqual((status, read["source"], read["sha256"]), (200, HOLD, written["sha256"]))
        status, body = self.command("script-write", {"key": "strategies/mine.py", "source": HOLD + "\n"})
        self.assertEqual((status, body["code"]), (400, "script_changed"))
        status, body = self.command("script-write", {"key": "strategies/mine.py", "source": HOLD + "# v2\n",
                                                     "base_sha256": read["sha256"]})
        self.assertEqual(status, 200)
        status, body = self.command("script-check", {"source": "class X:\n  def on_bar(self, c)\n"})
        self.assertEqual(body["problems"], ["Syntax error on line 2: expected ':'"])
        self.assertEqual(self.call("GET", "/api/v1/scripts/strategies/none.py")[1]["code"], "script_not_found")

    def test_paths_outside_script_folders_are_refused(self) -> None:
        for key, status, code in (("strategies/../cells/x.py", 400, "invalid_script_path"),
                                  ("cells/x.py", 400, "invalid_script_path"),
                                  ("strategies/.hidden/x.py", 403, "forbidden"),
                                  (".quantarium/x.py", 403, "forbidden"),
                                  ("../outside.py", 403, "forbidden"),
                                  ("strategies/notes.txt", 400, "invalid_script_path")):
            with self.subTest(key=key):
                got, body = self.command("script-write", {"key": key, "source": HOLD})
                self.assertEqual((got, body["code"]), (status, code))
        self.assertFalse((self.root / "cells" / "x.py").exists())

    def test_agents_write_only_their_folder_and_their_code_runs_only_sandboxed(self) -> None:
        token = self.agent_token()
        status, body = self.command("script-write", {"key": "strategies/user.py", "source": HOLD}, token)
        self.assertEqual((status, body["code"]), (403, "not_permitted"))
        status, body = self.command("script-write", {"key": "strategies/agent/__init__.py", "source": ""}, token)
        self.assertEqual((status, body["code"]), (400, "invalid_script_path"))
        status, body = self.command("script-write", {"key": "strategies/agent/hold.py", "source": HOLD}, token)
        self.assertEqual(status, 200, body)
        registry = json.loads((self.root / REGISTRY).read_text(encoding="utf-8"))
        self.assertEqual(registry[body["sha256"]]["state"], "agent-unreviewed")
        self.assertEqual(registry[body["sha256"]]["connection_name"], "Bot")

        api = self.server.api
        api.wait(api.submit("scenario-generate", {"output": "scenarios/m", "spec": SPEC})["id"])
        cell = api.command("cell-create", {"name": "Agent cell"})
        api.command("cell-save", {"id": cell["id"], "spec": {
            "mode": "simple", "symbols": ["AAA"], "initial_cash": 1000,
            "sleeves": [{"script": "strategies/agent/hold.py", "class": "Hold"}]}})
        # Static simulations run it in the sandbox, whoever starts them.
        done = api.wait(api.submit("deploy-suite", {"cells": [cell["id"]], "scenarios": ["scenarios/m"]})["id"])
        self.assertEqual(done["status"], "succeeded", done["error"])
        # Feed refuses it in the loader.
        feed = {"cells": [cell["id"]], "feed_id": "agent_forward", "provider": "scenario",
                "source": "scenarios/m", "start_date": "2030-01-07"}
        done = api.wait(api.submit("deploy-feed", feed)["id"])
        self.assertEqual((done["status"], done["error"]["code"]), ("failed", "script_unreviewed"))

        # The user's own edit changes the hash, so it runs as the user's code, in Feed too.
        read = self.call("GET", "/api/v1/scripts/strategies/agent/hold.py")[1]
        self.command("script-write", {"key": "strategies/agent/hold.py", "source": HOLD + "# reviewed\n",
                                      "base_sha256": read["sha256"]})
        done = api.wait(api.submit("deploy-feed", {**feed, "feed_id": "user_forward"})["id"])
        self.assertEqual(done["status"], "succeeded", done["error"])

    def test_the_user_reviews_an_agent_script_before_it_runs(self) -> None:
        token = self.agent_token()
        _, written = self.command("script-write", {"key": "strategies/agent/hold.py", "source": HOLD}, token)
        status, listed = self.call("GET", "/api/v1/agents/scripts")
        self.assertEqual([(item["key"], item["state"], item["current"]) for item in listed],
                         [("strategies/agent/hold.py", "agent-unreviewed", True)])
        scripts = {item["key"]: item for item in self.call("GET", "/api/v1/scripts")[1]}
        self.assertTrue(scripts["strategies/agent/hold.py"]["agent_unreviewed"])
        # Agents can neither list nor approve reviews.
        self.assertEqual(self.call("GET", "/api/v1/agents/scripts", token=token)[1]["code"], "not_permitted")
        status, body = self.command("script-review", {"sha256": written["sha256"]}, token)
        self.assertEqual((status, body["code"]), (403, "not_permitted"))

        self.assertEqual(self.command("script-review", {"sha256": written["sha256"]})[0], 200)
        scripts = {item["key"]: item for item in self.call("GET", "/api/v1/scripts")[1]}
        self.assertFalse(scripts["strategies/agent/hold.py"]["agent_unreviewed"])
        api = self.server.api
        api.wait(api.submit("scenario-generate", {"output": "scenarios/m", "spec": SPEC})["id"])
        cell = api.command("cell-create", {"name": "Reviewed"})
        api.command("cell-save", {"id": cell["id"], "spec": {
            "mode": "simple", "symbols": ["AAA"], "initial_cash": 1000,
            "sleeves": [{"script": "strategies/agent/hold.py", "class": "Hold"}]}})
        done = api.wait(api.submit("deploy-suite", {"cells": [cell["id"]], "scenarios": ["scenarios/m"]})["id"])
        self.assertEqual(done["status"], "succeeded", done["error"])
        self.assertEqual(self.command("script-review", {"sha256": "0" * 64})[1]["code"], "not_found")

    def test_agents_wait_while_the_tutorial_runs(self) -> None:
        token = self.agent_token()
        self.command("onboarding-start", {})
        status, body = self.call("GET", "/api/v1/cells", token=token)
        self.assertEqual((status, body["code"]), (403, "not_permitted"))
        self.assertIn("tutorial", body["error"])
        self.command("onboarding-finish", {"status": "skipped"})
        self.assertEqual(self.call("GET", "/api/v1/cells", token=token)[0], 200)

    def test_a_connection_allowed_to_edit_user_scripts_still_marks_them(self) -> None:
        token = self.agent_token(name="Editor", edit_user_scripts=True)
        status, body = self.command("script-write", {"key": "strategies/user.py", "source": HOLD}, token)
        self.assertEqual(status, 200, body)
        registry = json.loads((self.root / REGISTRY).read_text(encoding="utf-8"))
        self.assertEqual(registry[body["sha256"]]["key"], "strategies/user.py")

    def test_check_source(self) -> None:
        self.assertEqual(check_source("x = 1\n")["problems"],
                         ["No strategy class found: define a top-level class with an on_bar method"])
        result = check_source(HOLD)
        self.assertEqual((result["ok"], [item["name"] for item in result["classes"]]), (True, ["Hold"]))


if __name__ == "__main__":
    unittest.main()
