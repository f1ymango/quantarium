"""v0.9.0: research data folders from outside Quantarium are untrusted data.

Seeded mutations of a valid ``quantarium-data-v1`` folder. The reader may raise
only ValueError, and whatever it accepts must satisfy the format's rules.
"""

from __future__ import annotations

import json
import math
import os
import shutil
from pathlib import Path

from quantarium.userdata import MAX_TEXT, SURVIVORSHIP, read_user_data

from .harness import Fuzzer, MisuseCase, mutate_csv, mutate_json

SEED = int(os.environ.get("QUANTARIUM_FUZZ_SEED") or 2026)
ROUNDS = int(os.environ.get("QUANTARIUM_FUZZ_ROUNDS") or 6) * 10
SOURCE = {"format": "quantarium-data-v1", "supplier": "test", "license_note": "n", "survivorship": "survivorship_free",
          "description": "d", "as_of": "2030-01-10"}
PRICES = ("date,symbol,open,high,low,close,volume\n"
          "2030-01-07,AAA,10,11,9,10,100\n2030-01-07,BBB,20,21,19,20,100\n"
          "2030-01-08,AAA,10,11,9,10,100\n2030-01-08,BBB,20,21,19,20,100\n2030-01-08,CCC,5,6,4,5,100\n"
          "2030-01-09,AAA,10,11,9,10,100\n2030-01-09,CCC,5,6,4,5,100\n"
          "2030-01-10,AAA,10,11,9,10,100\n2030-01-10,CCC,5,6,4,5,100\n")
DELISTINGS = "symbol,date,price,reason\nBBB,2030-01-09,18.5,takeover\n"
MEMBERSHIP = "index,symbol,from,through\nIDX,AAA,2030-01-07,\nIDX,BBB,2030-01-07,2030-01-08\nIDX,CCC,2030-01-09,\n"


class UserDataFuzzTests(MisuseCase):
    def write(self, folder: Path, source: object = None, **texts: str) -> Path:
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
        (folder / "source.json").write_text(json.dumps(SOURCE if source is None else source), encoding="utf-8")
        for name, default in (("prices", PRICES), ("delistings", DELISTINGS), ("membership", MEMBERSHIP)):
            (folder / f"{name}.csv").write_text(texts.get(name, default), encoding="utf-8")
        return folder

    def check(self, failures: list, label: str, folder: Path) -> None:
        try:
            data = read_user_data(folder)
        except ValueError:
            return
        except Exception as error:  # anything else is a finding
            failures.append(f"{label}: {type(error).__name__}: {str(error)[:120]}")
            return
        if data.source["survivorship"] not in SURVIVORSHIP or any(
                isinstance(value, str) and len(value) > MAX_TEXT for value in data.source.values()):
            failures.append(f"{label}: accepted source {data.source}")
        position = {day: index for index, day in enumerate(data.days)}
        for symbol, (first, last) in data.spans.items():
            held = [day for day in data.days if symbol in data.tape[day]]
            if len(held) != position[last] - position[first] + 1:
                failures.append(f"{label}: accepted a gap in {symbol}")
        for bars in data.tape.values():
            for bar in bars.values():
                if not all(0 < value < math.inf for value in (bar.open, bar.high, bar.low, bar.close)):
                    failures.append(f"{label}: accepted bar {bar}")
        for symbol, delisting in data.delistings.items():
            if delisting.date <= data.spans[symbol][1] or (
                    delisting.price is not None and not 0 <= delisting.price < math.inf):
                failures.append(f"{label}: accepted delisting {delisting}")

    def test_the_valid_folder_reads(self) -> None:
        data = read_user_data(self.write(self.root / "valid"))
        self.assertEqual(sorted(data.spans), ["AAA", "BBB", "CCC"])
        self.assertEqual(data.delistings["BBB"].price, 18.5)

    def test_mutated_source_files(self) -> None:
        failures: list[str] = []
        for round_ in range(ROUNDS):
            seed = SEED * 1000 + 900 + round_
            folder = self.write(self.root / "fuzz", mutate_json(SOURCE, Fuzzer(seed, self.sentinel)))
            self.check(failures, f"source seed {seed}", folder)
        self.assertEqual(failures, [], "\n".join(failures))

    def test_mutated_tables(self) -> None:
        failures: list[str] = []
        for name, text in (("prices", PRICES), ("delistings", DELISTINGS), ("membership", MEMBERSHIP)):
            for round_ in range(ROUNDS):
                seed = SEED * 1000 + 950 + round_
                folder = self.write(self.root / "fuzz", **{name: mutate_csv(text, Fuzzer(seed, self.sentinel))})
                self.check(failures, f"{name} seed {seed}", folder)
        self.assertEqual(failures, [], "\n".join(failures))

    def test_hostile_strings_in_every_field(self) -> None:
        failures: list[str] = []
        for hostile in Fuzzer(SEED, self.sentinel).strings:
            for key in SOURCE:
                folder = self.write(self.root / "fuzz", {**SOURCE, key: hostile})
                self.check(failures, f"source {key}={hostile[:30]!r}", folder)
            cell = hostile.replace("\n", " ").replace(",", " ").replace('"', "'")
            for name, text in (("delistings", DELISTINGS), ("membership", MEMBERSHIP)):
                lines = text.splitlines()
                for column in range(len(lines[1].split(","))):
                    fields = lines[1].split(",")
                    fields[column] = cell
                    folder = self.write(self.root / "fuzz", **{name: "\n".join([lines[0], ",".join(fields), *lines[2:]]) + "\n"})
                    self.check(failures, f"{name} column {column}={cell[:30]!r}", folder)
        self.assertEqual(failures, [], "\n".join(failures))

    def test_duplicate_keys_constants_and_oversized_files_are_refused(self) -> None:
        folder = self.write(self.root / "dup")
        (folder / "source.json").write_text('{"format": "quantarium-data-v1", "supplier": "a", "supplier": "b"}',
                                            encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "repeats"):
            read_user_data(folder)
        (folder / "source.json").write_text('{"format": "quantarium-data-v1", "supplier": "a", "x": NaN}',
                                            encoding="utf-8")
        with self.assertRaises(ValueError):
            read_user_data(folder)
        (folder / "source.json").write_text(json.dumps({**SOURCE, "description": "x" * 70_000}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "larger than"):
            read_user_data(folder)
