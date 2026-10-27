"""Pin local strategy behavior to the exact file bytes that were hashed."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from quantarium.engine import _load_strategy


ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)


class StrategyLoadingTests(unittest.TestCase):
    def test_same_module_name_in_two_roots_and_source_edit(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as directory:
            first_root = Path(directory) / "first"
            second_root = Path(directory) / "second"
            first_root.mkdir()
            second_root.mkdir()
            first_path = first_root / "choice.py"
            second_path = second_root / "choice.py"
            first_path.write_text(
                "class Pick:\n    def on_bar(self, context):\n        return 0.25\n",
                encoding="utf-8",
            )
            second_path.write_text(
                "class Pick:\n    def on_bar(self, context):\n        return 0.75\n",
                encoding="utf-8",
            )
            first, first_hash = _load_strategy("choice:Pick", {}, first_root)
            second, second_hash = _load_strategy("choice:Pick", {}, second_root)
            self.assertEqual(first.on_bar(None), 0.25)
            self.assertEqual(second.on_bar(None), 0.75)
            self.assertEqual(first_hash, hashlib.sha256(first_path.read_bytes()).hexdigest())
            self.assertEqual(second_hash, hashlib.sha256(second_path.read_bytes()).hexdigest())
            self.assertNotEqual(first_hash, second_hash)

            first_path.write_text(
                "class Pick:\n    def on_bar(self, context):\n        return 0.5\n",
                encoding="utf-8",
            )
            revised, revised_hash = _load_strategy("choice:Pick", {}, first_root)
            self.assertEqual(first.on_bar(None), 0.25)
            self.assertEqual(revised.on_bar(None), 0.5)
            self.assertEqual(revised_hash, hashlib.sha256(first_path.read_bytes()).hexdigest())
            self.assertNotEqual(revised_hash, first_hash)


if __name__ == "__main__":
    unittest.main()
