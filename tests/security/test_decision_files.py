"""v0.6.0: decision files from outside Quantarium are untrusted data.

Seeded mutations of valid JSON and CSV decision files. The reader may raise
only ValueError, and whatever it accepts must satisfy the format's rules.
"""

from __future__ import annotations

import json
import math
import os
import unittest

from quantarium.decisions import FORMAT, MAX_NOTE, parse_decisions

from .harness import Fuzzer, MisuseCase, mutate_csv, mutate_json

SEED = int(os.environ.get("QUANTARIUM_FUZZ_SEED") or 2026)
ROUNDS = int(os.environ.get("QUANTARIUM_FUZZ_ROUNDS") or 6) * 20
VALID = {"format": FORMAT, "source": {"system": "s", "version": "1"}, "symbols": ["AAA", "BBB"],
         "decisions": [{"date": "2030-01-07", "weights": {"AAA": 0.5, "BBB": 0.3}, "note": "n"},
                       {"date": "2030-01-08", "weights": None,
                        "orders": [{"symbol": "AAA", "side": "sell", "kind": "stop", "shares": 5, "price": 9.0}]}]}
VALID_CSV = "date,symbol,weight\n2030-01-07,AAA,0.5\n2030-01-07,BBB,0.3\n2030-01-08,AAA,0.1\n"


class DecisionFileFuzzTests(MisuseCase):
    def check(self, failures: list, label: str, raw: bytes, kind: str) -> None:
        try:
            loaded = parse_decisions(raw, kind)
        except ValueError:
            return
        except Exception as error:  # anything else is a finding
            failures.append(f"{label}: {type(error).__name__}: {str(error)[:120]}")
            return
        for day, entry in loaded.decisions.items():
            weights = dict(entry.weights or {})
            if not all(isinstance(value, float) and math.isfinite(value) and 0 <= value <= 1
                       for value in weights.values()) or sum(weights.values()) > 1 + 1e-9:
                failures.append(f"{label}: accepted weights {weights} on {day}")
            if not set(weights) <= set(loaded.symbols) or len(entry.note) > MAX_NOTE:
                failures.append(f"{label}: accepted an entry outside the rules on {day}")
            for order in entry.orders or ():
                if order.shares < 1 or not math.isfinite(order.price) or order.price <= 0:
                    failures.append(f"{label}: accepted order {order}")

    def test_mutated_json_files(self) -> None:
        failures: list[str] = []
        for round_ in range(ROUNDS):
            seed = SEED * 1000 + 600 + round_
            value = mutate_json(VALID, Fuzzer(seed, self.sentinel))
            self.check(failures, f"json seed {seed}", json.dumps(value).encode(), "json")
        self.assertEqual(failures, [], "\n".join(failures))

    def test_mutated_csv_files(self) -> None:
        failures: list[str] = []
        for round_ in range(ROUNDS):
            seed = SEED * 1000 + 700 + round_
            self.check(failures, f"csv seed {seed}", mutate_csv(VALID_CSV, Fuzzer(seed, self.sentinel)).encode(), "csv")
        self.assertEqual(failures, [], "\n".join(failures))

    def test_hostile_strings_in_every_field(self) -> None:
        failures: list[str] = []
        fuzzer = Fuzzer(SEED, self.sentinel)
        for hostile in fuzzer.strings:
            for path in (("symbols", 0), ("decisions", 0, "date"), ("decisions", 0, "note"), ("source", "system"),
                         ("decisions", 1, "orders", 0, "symbol"), ("decisions", 1, "orders", 0, "tag")):
                value = json.loads(json.dumps(VALID))
                node = value
                for key in path[:-1]:
                    node = node[key]
                node[path[-1]] = hostile
                self.check(failures, f"{path} = {hostile[:30]!r}", json.dumps(value).encode(), "json")
        self.assertEqual(failures, [], "\n".join(failures))


class DecisionPathTests(MisuseCase):
    def test_agents_cannot_point_a_decision_sleeve_outside_the_workspace_or_into_hidden_folders(self) -> None:
        status, cell = self.agent("command", "cell-create", {"name": "Replay"})
        self.assertEqual(status, 200, cell)
        allowed = []
        for key in (".quantarium/agents.json", "../outside/sentinel.txt", str(self.sentinel), "decisions/../.quantarium/x.csv",
                    "decisions\\..\\..\\outside\\sentinel.txt"):
            status, body = self.agent("command", "cell-save", {"id": cell["id"], "spec": {
                "mode": "simple", "symbols": ["AAA"], "sleeves": [{"decisions": key}]}})
            if status < 400:
                status, body = self.agent("get", f"cells/{cell['id']}")
                if "not found" not in json.dumps(body) and "belong in" not in json.dumps(body):
                    allowed.append(f"{key}: {str(body)[:120]}")
        self.assertEqual(allowed, [])


if __name__ == "__main__":
    unittest.main()
