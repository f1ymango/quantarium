"""The sandbox for unreviewed strategies: its code policy, and its runtime
layer tested on its own, with the policy switched off."""

from __future__ import annotations

import os
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import MappingProxyType
from unittest import mock

from quantarium import sandbox
from quantarium.model import Bar, Context
from quantarium.sandbox import SandboxError, SandboxedStrategy, policy_problems


ROOT = Path(__file__).resolve().parents[1]


def context(day: int, history: list[Bar]) -> Context:
    bar = Bar(date(2030, 1, 7) + timedelta(days=day), "AAA", 10.0 + day, 11.0 + day, 9.0 + day,
              10.0 + day, 1000)
    history.append(bar)
    return Context(date=bar.date, bars=MappingProxyType({"AAA": bar}),
                   history=MappingProxyType({"AAA": tuple(history)}), cash=1000.0,
                   positions=MappingProxyType({"AAA": 0}), equity=1000.0)


def strategy(body: str, prelude: str = "") -> bytes:
    return (prelude + "class S:\n    def on_bar(self, context):\n" +
            "".join(f"        {line}\n" for line in body.splitlines())).encode()


class PolicyTests(unittest.TestCase):
    def test_starter_strategies_pass(self) -> None:
        source = (ROOT / "quantarium" / "assets" / "starter" / "strategies.py").read_text(encoding="utf-8")
        self.assertEqual(policy_problems(source), [])

    def test_dangerous_constructs_are_flagged(self) -> None:
        for source, expected in (
                ("import os\n", "import os"), ("import subprocess as s\n", "import subprocess"),
                ("from os import path\n", "from os import"), ("from . import x\n", "from . import"),
                ("x = open('f')\n", "open is not allowed"), ("eval('1')\n", "eval is not allowed"),
                ("getattr(1, 'real')\n", "getattr is not allowed"),
                ("x = ().__class__.__bases__\n", ".__class__ is not allowed"),
                ("def f():\n    global y\n", "global is not allowed"),
                ("x = __builtins__\n", "__builtins__ is not allowed")):
            with self.subTest(source=source):
                problems = policy_problems(source)
                self.assertTrue(any(expected in problem for problem in problems), problems)
                self.assertTrue(problems[-1].startswith("Allowed imports:"))
        self.assertEqual(policy_problems("import math\nfrom collections import deque\n"), [])

    def test_a_policy_problem_stops_the_strategy_before_any_process_starts(self) -> None:
        with mock.patch("subprocess.Popen") as popen:
            with self.assertRaises(SandboxError) as caught:
                SandboxedStrategy(strategy("return 1.0", "import os\n"), "S", {}, "bad.py")
        popen.assert_not_called()
        self.assertEqual(caught.exception.code, "sandbox_policy")


@mock.patch.object(sandbox, "policy_problems", lambda source, filename="": [])
class RuntimeLayerTests(unittest.TestCase):
    """Each attack below would be caught by the policy; here only the runtime layer stands."""

    def run_one(self, source: bytes, days: int = 1):
        runner = SandboxedStrategy(source, "S", {}, "attack.py")
        history: list[Bar] = []
        try:
            result = None
            for day in range(days):
                result = runner.on_bar(context(day, history))
            return result
        finally:
            runner.close()

    def assertRefused(self, source: bytes, event: str) -> None:
        with self.assertRaises(SandboxError) as caught:
            self.run_one(source)
        self.assertIn(event, str(caught.exception))

    def test_files_processes_and_imports_are_refused_after_loading(self) -> None:
        self.assertRefused(strategy("open('stolen.txt', 'w')\nreturn 1.0"), "open")
        self.assertRefused(strategy("os.listdir('.')\nreturn 1.0", "import os\n"), "os.listdir")
        self.assertRefused(strategy("os.system('echo hi')\nreturn 1.0", "import os\n"), "os.system")
        self.assertRefused(strategy("__import__('subprocess')\nreturn 1.0"), "import")
        self.assertRefused(strategy("return 1.0", "import socket\n"), "import")
        self.assertRefused(strategy("import ctypes\nreturn 1.0"), "import")

    def test_ctypes_is_refused_even_when_an_allowed_module_loaded_it(self) -> None:
        from quantarium import sandbox_child

        # numpy imports ctypes for itself; the child drops it from the cache before the hook goes on.
        modules = {"ctypes": object(), "_ctypes": object(), "ctypes.util": object(), "math": object()}
        sandbox_child._forget_unsafe_modules(modules)
        self.assertEqual(list(modules), ["math"])
        source = Path(sandbox_child.__file__).read_text(encoding="utf-8")
        main = source[source.index("def main()"):]
        self.assertLess(main.index("_forget_unsafe_modules(sys.modules)"), main.index("sys.addaudithook(_hook)"))
        self.assertRefused(strategy("return 1.0", "import ctypes\n"), "import")

    def test_no_secrets_reach_the_child(self) -> None:
        with mock.patch.dict(os.environ, {"APCA_API_SECRET_KEY": "secret", "ANTHROPIC_API_KEY": "sk-ant-x"}):
            source = strategy("return 0.0 if any(key in os.environ for key in "
                              "('APCA_API_SECRET_KEY', 'ANTHROPIC_API_KEY')) else 1.0", "import os\n")
            self.assertEqual(self.run_one(source), 1.0)

    def test_printing_cannot_reach_the_protocol(self) -> None:
        source = strategy("print('{\"ok\": true, \"weights\": 0.0}')\nimport sys\n"
                          "sys.stdout.write('junk\\n')\nreturn 0.5")
        self.assertEqual(self.run_one(source, days=3), 0.5)

    def test_a_runaway_strategy_is_stopped(self) -> None:
        with mock.patch.object(sandbox, "CALL_TIMEOUT", 1.0):
            runner = SandboxedStrategy(strategy("while True:\n    pass"), "S", {}, "loop.py")
            with self.assertRaises(SandboxError) as caught:
                runner.on_bar(context(0, []))
            self.assertIn("longer than 1 seconds", str(caught.exception))
            self.assertIsNotNone(runner._process.wait(timeout=10))

    def test_memory_is_limited(self) -> None:
        self.assertRefused(strategy("block = bytearray(3 * 1024 ** 3)\nreturn 1.0"), "")

    def test_history_and_state_survive_the_boundary(self) -> None:
        source = (b"class S:\n"
                  b"    def __init__(self, window=3):\n        self.window = window\n        self.calls = 0\n"
                  b"    def on_bar(self, context):\n        self.calls += 1\n"
                  b"        closes = [bar.close for bar in context.history['AAA']]\n"
                  b"        return {'AAA': min(1.0, len(closes) / 100)}\n"
                  b"    def snapshot(self):\n        return {'calls': self.calls}\n"
                  b"    def restore(self, state):\n        self.calls = state['calls']\n")
        runner = SandboxedStrategy(source, "S", {"window": 5}, "state.py")
        try:
            history: list[Bar] = []
            weights = [runner.on_bar(context(day, history)) for day in range(30)]
            self.assertEqual(weights[-1], {"AAA": 0.3})
            self.assertEqual(runner.snapshot(), {"calls": 30})
            runner.restore({"calls": 2})
            self.assertEqual(runner.snapshot(), {"calls": 2})
        finally:
            runner.close()


if __name__ == "__main__":
    unittest.main()
