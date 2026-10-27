"""v0.5.2: agent-written code never runs inside Quantarium's own process unless the user reviewed it.

The scripts here are harmless (they return a weight). The tests detect
where a script ran: a module loaded in this process appears in
``sys.modules``; a sandboxed one does not.
"""

from __future__ import annotations

import hashlib
import importlib.util
import secrets
import sys
import unittest
from pathlib import Path

from .harness import MisuseCase

STRATEGY = "class S:\n    def on_bar(self, context):\n        return 1.0\n"


def scenario_spec() -> dict:
    return {"name": "plain", "seed": 3, "start_date": "2030-01-07", "symbols": ["AAA"],
            "segments": [{"sessions": 20, "annual_drift": 0.1, "annual_volatility": 0.2}]}


class CodeExecutionTests(MisuseCase):
    def setUp(self) -> None:
        super().setUp()
        job = self.api.submit("scenario-generate", {"output": "scenarios/plain", "spec": scenario_spec()})
        self.assertEqual(self.api.wait(job["id"])["status"], "succeeded")
        self.path_before = list(sys.path)

    def tearDown(self) -> None:
        sys.path[:] = self.path_before
        for name in [name for name, module in sys.modules.items() if self.in_workspace(module)]:
            del sys.modules[name]
        super().tearDown()

    def in_workspace(self, module) -> bool:
        origin = getattr(module, "__file__", None)
        try:
            return bool(origin) and self.root.resolve() in Path(origin).resolve().parents
        except (OSError, ValueError):
            return False

    def loaded_here(self) -> list[str]:
        """Workspace scripts that were imported into this process."""
        return sorted(Path(module.__file__).name for module in list(sys.modules.values()) if self.in_workspace(module))

    def write_as_agent(self, key: str, source: str = STRATEGY) -> tuple[int, object]:
        return self.agent("command", "script-write", {"key": key, "source": source})

    def run_cell(self, script: str, *, as_agent: bool = True) -> dict:
        status, cell = self.agent("command", "cell-create", {"name": f"Cell {secrets.token_hex(3)}"})
        self.assertEqual(status, 200, cell)
        spec = {"mode": "simple", "symbols": ["AAA"], "initial_cash": 1000, "sleeves": [{"script": script, "class": "S"}]}
        status, saved = self.agent("command", "cell-save", {"id": cell["id"], "spec": spec})
        self.assertEqual(status, 200, saved)
        params = {"cells": [cell["id"]], "data": "scenarios/plain/prices.csv"}
        if as_agent:
            status, job = self.agent("job", "deploy-static", params)
        else:
            status, job = self.call("POST", "/api/v1/jobs", {"action": "deploy-static", "params": params})
        self.assertEqual(status, 202, job)
        return self.api.wait(job["id"])

    def test_no_spelling_of_an_agent_script_path_runs_it_in_process(self) -> None:
        spellings = ["strategies/agent/plain.py", "strategies/agent/./dotted.py", "strategies/agent//doubled.py",
                     "STRATEGIES/agent/upper.py", "strategies/AGENT/upper2.py", "strategies\\agent\\backslash.py",
                     "strategies/agent/../agent/parent.py", "strategies/agent/Mixed.py"]
        written = []
        for key in spellings:
            status, body = self.write_as_agent(key)
            if status == 200:
                written.append(key)
        self.assertTrue(written)
        for path in sorted((self.root / "strategies" / "agent").glob("*.py")):
            for script in (f"strategies/agent/{path.name}", f"strategies/agent/{path.name.lower()}"):
                job = self.run_cell(script)
                self.assertEqual(job["status"], "succeeded", f"{script}: {job['error']}")
        self.assertEqual(self.loaded_here(), [], "an unreviewed agent script ran in Quantarium's process")

    def test_a_reviewed_script_cannot_bring_in_an_unreviewed_one(self) -> None:
        helper = f"helper_{secrets.token_hex(3)}"
        main = f"import {helper}\n\n\nclass S:\n    def on_bar(self, context):\n        return 1.0\n"
        self.assertEqual(self.write_as_agent(f"strategies/agent/{helper}.py")[0], 200)
        self.assertEqual(self.write_as_agent("strategies/agent/main.py", main)[0], 200)
        # The user reviews main.py only.
        digest = hashlib.sha256(main.encode()).hexdigest()
        self.assertEqual(self.call("POST", "/api/v1/commands", {"command": "script-review",
                                                                "params": {"sha256": digest}})[0], 200)
        self.run_cell("strategies/agent/main.py", as_agent=False)
        self.assertNotIn(f"{helper}.py", self.loaded_here(), "a reviewed script imported unreviewed agent code")

    def test_the_agent_folder_never_shadows_the_servers_imports(self) -> None:
        self.assertEqual(self.write_as_agent("strategies/agent/plain.py")[0], 200)
        self.assertEqual(self.run_cell("strategies/agent/plain.py")["status"], "succeeded")
        probe = f"blprobe_{secrets.token_hex(4)}"
        self.assertEqual(self.write_as_agent(f"strategies/agent/{probe}.py", "VALUE = 1\n")[0], 200)
        # A module name the server might import later (as it does keyring) must not resolve to agent code.
        self.assertIsNone(importlib.util.find_spec(probe), "agent-written files are importable by the server")
        sys.path.insert(0, str(self.root / "strategies" / "agent"))
        try:
            # Even with the folder on the path, the import guard refuses the unreviewed file.
            with self.assertRaises(Exception) as caught:
                importlib.import_module(probe)
            self.assertIn("not been reviewed", str(caught.exception))
        finally:
            sys.path.remove(str(self.root / "strategies" / "agent"))

    def test_agents_cannot_write_files_python_runs_by_itself(self) -> None:
        accepted = []
        for key in ("strategies/agent/evil.pth", "strategies/agent/evil.pyc", "strategies/agent/__init__.py",
                    "strategies/agent/sub/__init__.py", "strategies/agent/evil.pyw", "strategies/agent/evil.py.",
                    "sitecustomize.py", "usercustomize.py", ".quantarium/x.py",
                    "../outside/evil.py", str(self.outside / "evil.py"),
                    # Windows forms: an alternate data stream, a short 8.3 name, and a device name. (Windows 11
                    # treats con.py as an ordinary file, which provenance records like any other.)
                    "strategies/agent/plain.py:hidden", "strategies/agent/plain.py::$DATA", "STRATE~1/agent/short.py",
                    "strategies/agent/nul"):
            status, _ = self.write_as_agent(key, "VALUE = 1\n")
            if status < 400:
                accepted.append(key)
        self.assertEqual(accepted, [])

    def test_an_unreviewed_script_that_reaches_for_secrets_is_refused(self) -> None:
        canary_file = self.canaries.home / ".ssh" / "id_ed25519"
        attempts = {
            "reads.py": f"class S:\n    def on_bar(self, context):\n        return float(len(open({str(canary_file)!r}).read()))\n",
            "imports.py": "import os\n\n\nclass S:\n    def on_bar(self, context):\n        return float(len(os.environ))\n",
            "sockets.py": "import socket\n\n\nclass S:\n    def on_bar(self, context):\n        return 1.0\n",
            "dunder.py": "class S:\n    def on_bar(self, context):\n        return ().__class__.__bases__[0]\n",
        }
        for name, source in attempts.items():
            self.assertEqual(self.write_as_agent(f"strategies/agent/{name}", source)[0], 200)
            job = self.run_cell(f"strategies/agent/{name}")
            self.assertEqual(job["status"], "failed", name)
            self.assertIn("sandbox", str(job["error"]).lower(), name)
        self.assertEqual(self.loaded_here(), [])

    def test_model_cards_and_allocators_follow_the_same_rules(self) -> None:
        status, cell = self.agent("command", "cell-create", {"name": "Cards"})
        refused = []
        for card in ("../outside/sentinel.txt", str(self.sentinel), ".quantarium/agents.json",
                     str(self.canaries.home / ".aws" / "credentials")):
            spec = {"mode": "simple", "symbols": ["AAA"], "model_card": card}
            status, body = self.agent("command", "cell-save", {"id": cell["id"], "spec": spec})
            if status < 400:
                status, body = self.agent("get", f"cells/{cell['id']}")
            refused.append((card, status))
        self.assertEqual(self.write_as_agent("strategies/agent/alloc.py",
                                             "class A:\n    def allocate(self, context, sleeves):\n"
                                             "        return {name: 1.0 for name in sleeves}\n")[0], 200)
        self.assertEqual(self.write_as_agent("strategies/agent/plain.py")[0], 200)
        spec = {"mode": "complex", "symbols": ["AAA"], "initial_cash": 1000,
                "sleeves": [{"script": "strategies/agent/plain.py", "class": "S", "symbols": ["AAA"]}],
                "allocator": {"script": "strategies/agent/alloc.py", "class": "A"}}
        status, body = self.agent("command", "cell-save", {"id": cell["id"], "spec": spec})
        if status == 200:
            status, job = self.agent("job", "deploy-static", {"cells": [cell["id"]], "data": "scenarios/plain/prices.csv"})
            if status == 202:
                self.api.wait(job["id"])
        self.assertEqual(self.loaded_here(), [])


if __name__ == "__main__":
    unittest.main()
