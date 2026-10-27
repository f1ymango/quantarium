"""v0.9.3: trades files from outside Quantarium are untrusted data.

Seeded mutations of a valid trades CSV. Reading and converting may raise
only ValueError, and whatever converts must read back as a valid decision
file with whole, positive shares.
"""

from __future__ import annotations

import json
import os

from quantarium.decisions import parse_decisions
from quantarium.trades import read_trades, trades_to_decisions

from .harness import Fuzzer, MisuseCase, mutate_csv

SEED = int(os.environ.get("QUANTARIUM_FUZZ_SEED") or 2026)
ROUNDS = int(os.environ.get("QUANTARIUM_FUZZ_ROUNDS") or 6) * 20
VALID = ("date,symbol,side,shares,price\n2024-03-04,AAA,buy,10,100.5\n2024-03-05,AAA,sell,4,101\n"
         "2024-03-05,BBB,buy,3,50\n2024-03-06,BBB,sell,3,49.5\n")


class TradesFuzzTests(MisuseCase):
    def check(self, failures: list, label: str, text: str) -> None:
        path = self.root / "trades.csv"
        path.write_text(text, encoding="utf-8")
        try:
            document, _ = trades_to_decisions(read_trades(path))
        except ValueError:
            return
        except Exception as error:  # anything else is a finding
            failures.append(f"{label}: {type(error).__name__}: {str(error)[:120]}")
            return
        loaded = parse_decisions(json.dumps(document).encode())
        for day, entry in loaded.decisions.items():
            if entry.weights is not None or any(order.shares < 1 or order.kind != "market" for order in entry.orders):
                failures.append(f"{label}: produced {entry} on {day}")

    def test_mutated_trades_files(self) -> None:
        failures: list[str] = []
        for round_ in range(ROUNDS):
            seed = SEED * 1000 + 800 + round_
            self.check(failures, f"trades seed {seed}", mutate_csv(VALID, Fuzzer(seed, self.sentinel)))
        self.assertEqual(failures, [], "\n".join(failures))

    def test_hostile_strings_in_every_column(self) -> None:
        failures: list[str] = []
        lines = VALID.splitlines()
        for hostile in Fuzzer(SEED, self.sentinel).strings:
            cell = hostile.replace("\n", " ").replace(",", " ").replace('"', "'")
            for column in range(5):
                fields = lines[1].split(",")
                fields[column] = cell
                self.check(failures, f"column {column}={cell[:30]!r}", "\n".join([lines[0], ",".join(fields), *lines[2:]]) + "\n")
        self.assertEqual(failures, [], "\n".join(failures))
