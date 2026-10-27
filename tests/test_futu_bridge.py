"""v0.9.4: the Futu bridge, tested with fake candles; Futu itself is never installed or called."""

from __future__ import annotations

import ast
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from quantarium.data import load_daily_csv

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "tools" / "futu_bridge" / "bridge.py"
spec = importlib.util.spec_from_file_location("quantarium_futu_bridge", BRIDGE)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def candle(day: str, close: float, volume: int = 1000) -> dict:
    return {"code": "US.AAPL", "time_key": f"{day} 00:00:00", "open": close, "high": close + 1,
            "low": close - 1, "close": close, "volume": volume, "turnover": 0.0}


class FutuBridgeTests(unittest.TestCase):
    def test_importing_the_bridge_does_not_import_futu(self) -> None:
        self.assertNotIn("futu", sys.modules)

    def test_the_bridge_has_no_trading_code(self) -> None:
        source = BRIDGE.read_text(encoding="utf-8")
        names = {node.attr for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Attribute)}
        names |= {node.id for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Name)}
        for forbidden in ("OpenSecTradeContext", "OpenHKTradeContext", "OpenUSTradeContext", "OpenFutureTradeContext",
                          "place_order", "unlock_trade", "modify_order", "TrdEnv"):
            self.assertNotIn(forbidden, names)
            self.assertNotIn(forbidden, source)

    def test_only_loopback_hosts_are_accepted(self) -> None:
        for host in ("192.168.1.5", "openapi.futunn.com", "0.0.0.0"):
            with self.subTest(host=host), self.assertRaisesRegex(ValueError, "only to OpenD on this computer"):
                bridge.fetch({"US.AAPL": "AAPL"}, None, None, host=host, port=11111, adjust="forward")

    def test_codes_map_to_quantarium_symbols(self) -> None:
        self.assertEqual(bridge.parse_codes("US.AAPL, hk.00700=tencent"), {"US.AAPL": "AAPL", "HK.00700": "TENCENT"})
        for text in ("AAPL", "US.AAPL,US.AAPL", "XX.AAPL", "US.AAPL=../x", ""):
            with self.subTest(text=text), self.assertRaises(ValueError):
                bridge.parse_codes(text)

    def test_candles_become_a_price_file_quantarium_reads(self) -> None:
        rows = bridge.to_rows("AAPL", [candle("2024-03-05", 101), candle("2024-03-04", 100)])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "futu.csv"
            self.assertEqual(bridge.write(path, rows, append=False), 2)
            days, tape, _ = load_daily_csv(path, {"AAPL"})
            self.assertEqual([day.isoformat() for day in days], ["2024-03-04", "2024-03-05"])
            self.assertEqual(tape[days[1]]["AAPL"].close, 101)
            with self.assertRaises(FileExistsError):
                bridge.write(path, rows, append=False)
            more = bridge.to_rows("AAPL", [candle("2024-03-05", 999), candle("2024-03-06", 102)])
            self.assertEqual(bridge.write(path, more, append=True), 1)  # 2024-03-05 is kept as first seen
            days, tape, _ = load_daily_csv(path, {"AAPL"})
            self.assertEqual((len(days), tape[days[1]]["AAPL"].close), (3, 101))

    def test_bad_candles_are_refused(self) -> None:
        for record in ({**candle("2024-03-04", 100), "close": float("nan")},
                       {**candle("2024-03-04", 100), "low": 120},
                       {**candle("2024-03-04", 100), "volume": -5}):
            with self.subTest(record=record), self.assertRaises(ValueError):
                bridge.to_rows("AAPL", [record])
        with self.assertRaisesRegex(ValueError, "two candles"):
            bridge.to_rows("AAPL", [candle("2024-03-04", 100), candle("2024-03-04", 100)])


if __name__ == "__main__":
    unittest.main()
