"""Decision files: the format other systems use to hand Quantarium their decisions (v0.6.0)."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from unittest import mock

from quantarium.__main__ import main
from quantarium.decisions import FORMAT, load_decisions, parse_decisions

ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)


def document(**patch) -> dict:
    return {"format": FORMAT,
            "source": {"system": "my-agent", "version": "0.3", "model": "m", "training_cutoff": "2025-01-31"},
            "symbols": ["AAA", "BBB"],
            "decisions": [
                {"date": "2030-01-08", "weights": None},
                {"date": "2030-01-07", "weights": {"AAA": 0.5, "BBB": 0.3}, "note": "trend up"},
                {"date": "2030-01-09", "weights": {"AAA": 0.2},
                 "orders": [{"symbol": "BBB", "side": "buy", "kind": "limit", "shares": 10, "price": 9.5,
                             "expires_after_sessions": 3}]}],
            **patch}


def parse(value: object) -> object:
    return parse_decisions(json.dumps(value).encode())


class DecisionFileTests(unittest.TestCase):
    def test_a_json_file_is_read_in_date_order(self) -> None:
        loaded = parse(document())
        self.assertEqual(loaded.dates, (date(2030, 1, 7), date(2030, 1, 8), date(2030, 1, 9)))
        self.assertEqual(dict(loaded.entry(date(2030, 1, 7)).weights), {"AAA": 0.5, "BBB": 0.3})
        self.assertIsNone(loaded.entry(date(2030, 1, 8)).weights)
        self.assertIsNone(loaded.entry(date(2030, 1, 10)))
        order = loaded.entry(date(2030, 1, 9)).orders[0]
        self.assertEqual((order.symbol, order.kind, order.shares, order.price, order.expires_after_sessions),
                         ("BBB", "limit", 10, 9.5, 3))
        summary = loaded.summary()
        self.assertEqual((summary["decisions"], summary["orders"], summary["first_date"], summary["last_date"]),
                         (3, 1, "2030-01-07", "2030-01-09"))
        self.assertEqual(len(summary["sha256"]), 64)

    def test_a_csv_file_carries_weights(self) -> None:
        raw = "﻿Date,Symbol,Weight\n2030-01-07,aaa,0.5\n2030-01-07,BBB,0.25\n\n2030-01-08,AAA,0\n".encode()
        loaded = parse_decisions(raw, "csv")
        self.assertEqual(loaded.symbols, ("AAA", "BBB"))
        self.assertEqual(dict(loaded.entry(date(2030, 1, 7)).weights), {"AAA": 0.5, "BBB": 0.25})
        self.assertEqual(dict(loaded.entry(date(2030, 1, 8)).weights), {"AAA": 0.0})

    def test_bad_files_are_refused_with_a_reason(self) -> None:
        cases = {
            "format": document(format="other"),
            "unknown field": document(extra=1),
            "symbols": document(symbols=[]),
            "repeated symbol": document(symbols=["AAA", "AAA"]),
            "unlisted symbol": document(decisions=[{"date": "2030-01-07", "weights": {"CCC": 0.5}}]),
            "bad symbol": document(symbols=["AAA", "../x"]),
            "date": document(decisions=[{"date": "07/01/2030", "weights": None}]),
            "repeated date": document(decisions=[{"date": "2030-01-07", "weights": None}] * 2),
            "over one": document(decisions=[{"date": "2030-01-07", "weights": {"AAA": 0.7, "BBB": 0.4}}]),
            "negative": document(decisions=[{"date": "2030-01-07", "weights": {"AAA": -0.1}}]),
            "text weight": document(decisions=[{"date": "2030-01-07", "weights": {"AAA": "half"}}]),
            "boolean weight": document(decisions=[{"date": "2030-01-07", "weights": {"AAA": True}}]),
            "no decisions": document(decisions=[]),
            "order kind": document(decisions=[{"date": "2030-01-07", "orders": [
                {"symbol": "AAA", "side": "short", "kind": "limit", "shares": 1, "price": 1}]}]),
            "order shares": document(decisions=[{"date": "2030-01-07", "orders": [
                {"symbol": "AAA", "side": "buy", "kind": "limit", "shares": 1.5, "price": 1}]}]),
            "order price": document(decisions=[{"date": "2030-01-07", "orders": [
                {"symbol": "AAA", "side": "buy", "kind": "stop", "shares": 1, "price": 0}]}]),
            "long note": document(decisions=[{"date": "2030-01-07", "weights": None, "note": "x" * 501}]),
            "control note": document(decisions=[{"date": "2030-01-07", "weights": None, "note": "a\x00b"}]),
            "source field": document(source={"author": "me"}),
            "source cutoff": document(source={"training_cutoff": "soon"}),
        }
        for name, value in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                parse(value)
        for raw in (b'{"format": "quantarium-decisions-v1", "weights": NaN}', b"\xff\xfe", b"[" * 1000, b"[]",
                    b"not json"):
            with self.subTest(raw=raw[:20]), self.assertRaises(ValueError):
                parse_decisions(raw)
        for raw in (b"date,symbol\n2030-01-07,AAA\n", b"date,symbol,weight\n2030-01-07,AAA\n",
                    b"date,symbol,weight\n2030-01-07,AAA,0.5\n2030-01-07,AAA,0.2\n",
                    b"date,symbol,weight\n2030-01-07,AAA,nan\n", b"date,symbol,weight\n", b""):
            with self.subTest(csv=raw[:30]), self.assertRaises(ValueError):
                parse_decisions(raw, "csv")

    def test_files_are_checked_by_suffix_and_size(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            text = Path(temp) / "decisions.txt"
            text.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_decisions(text)
            big = Path(temp) / "big.json"
            with big.open("wb") as handle:
                handle.truncate(11 * 1024 * 1024)
            with self.assertRaises(ValueError):
                load_decisions(big)

    def test_the_command_line_check(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as temp:
            good = Path(temp) / "decisions.json"
            good.write_text(json.dumps(document()), encoding="utf-8")
            output = io.StringIO()
            with mock.patch("sys.argv", ["quantarium", "decisions", "check", str(good)]), redirect_stdout(output):
                main()
            self.assertEqual(json.loads(output.getvalue())["decisions"], 3)
            bad = Path(temp) / "bad.json"
            bad.write_text("{}", encoding="utf-8")
            with mock.patch("sys.argv", ["quantarium", "decisions", "check", str(bad)]), \
                    self.assertRaises(SystemExit) as caught:
                main()
            self.assertIn("Invalid decision file", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
