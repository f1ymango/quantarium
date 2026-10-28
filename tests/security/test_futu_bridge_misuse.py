"""The Futu bridge refuses unsafe connections and output paths without reaching OpenD."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
from datetime import date
from pathlib import Path
from unittest import mock

from .harness import MisuseCase

ROOT = Path(__file__).resolve().parents[2]
BRIDGE = ROOT / "tools" / "futu_bridge" / "bridge.py"
spec = importlib.util.spec_from_file_location("quantarium_futu_bridge_misuse", BRIDGE)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class FakeQuoteContext:
    calls: list[tuple[str, int]] = []

    def __init__(self, *, host: str, port: int) -> None:
        self.calls.append((host, port))

    def request_history_kline(self, *args, **kwargs):
        return 0, types.SimpleNamespace(to_dict=lambda _: []), None

    def close(self) -> None:
        pass


FAKE_FUTU = types.SimpleNamespace(OpenQuoteContext=FakeQuoteContext, RET_OK=0,
                                  KLType=types.SimpleNamespace(K_DAY="day"),
                                  AuType=types.SimpleNamespace(QFQ="forward", NONE="none"))


class FutuBridgeMisuseTests(MisuseCase):
    def test_only_literal_loopback_addresses_reach_quote_context(self) -> None:
        FakeQuoteContext.calls.clear()
        with mock.patch.dict(sys.modules, {"futu": FAKE_FUTU}):
            for host, expected in (("127.0.0.1", "127.0.0.1"), ("localhost", "127.0.0.1"), ("::1", "::1")):
                with self.subTest(host=host):
                    bridge.fetch({"US.AAPL": "AAPL"}, date(2024, 3, 4), date(2024, 3, 8),
                                 host=host, port=11111, adjust="none")
                    self.assertEqual(FakeQuoteContext.calls[-1], (expected, 11111))
            for host in ("127.0.0.2", "0.0.0.0", "localhost.example", "openapi.futunn.com"):
                with self.subTest(host=host), self.assertRaisesRegex(ValueError, "only to OpenD on this computer"):
                    bridge.fetch({"US.AAPL": "AAPL"}, date(2024, 3, 4), date(2024, 3, 8),
                                 host=host, port=11111, adjust="none")
        self.assertEqual(len(FakeQuoteContext.calls), 3)

    def test_invalid_ports_are_refused_before_import(self) -> None:
        for port in (0, -1, 65536):
            with self.subTest(port=port), self.assertRaisesRegex(ValueError, "port.*1.*65535"):
                bridge.fetch({"US.AAPL": "AAPL"}, date(2024, 3, 4), date(2024, 3, 8),
                             host="127.0.0.1", port=port, adjust="none")

    def test_checkout_output_must_be_in_private_data_folders(self) -> None:
        forbidden = (ROOT / "README.md", ROOT / "data" / ".." / "README.md", ROOT / "docs" / "bars.csv")
        for path in forbidden:
            with self.subTest(path=path), mock.patch.object(
                    bridge, "fetch", side_effect=AssertionError("fetch must not run")):
                with self.assertRaisesRegex(ValueError, "--output"):
                    bridge.main(["--codes", "US.AAPL", "--from", "2024-03-04", "--through", "2024-03-08",
                                 "--output", str(path)])
        for folder in ("data", "scenarios", "private"):
            with self.subTest(folder=folder):
                self.assertEqual(bridge.checked_output(ROOT / folder / "bars.csv"),
                                 (ROOT / folder / "bars.csv").resolve())

    def test_symlink_into_checkout_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            link = Path(folder) / "link"
            try:
                link.symlink_to(ROOT / "docs", target_is_directory=True)
            except OSError:
                # Windows may disallow symlinks; model the resolved target in that case.
                original_resolve = Path.resolve

                def resolved(path, *args, **kwargs):
                    if path == link / "bars.csv":
                        return ROOT / "docs" / "bars.csv"
                    return original_resolve(path, *args, **kwargs)

                with mock.patch.object(Path, "resolve", resolved):
                    with self.assertRaisesRegex(ValueError, "--output"):
                        bridge.checked_output(link / "bars.csv")
            else:
                with self.assertRaisesRegex(ValueError, "--output"):
                    bridge.checked_output(link / "bars.csv")
