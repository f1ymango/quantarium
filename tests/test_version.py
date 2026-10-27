"""The version is the same wherever it is written (docs/versioning.md)."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import quantarium

ROOT = Path(__file__).resolve().parents[1]


class VersionTests(unittest.TestCase):
    def test_every_place_names_the_same_version(self) -> None:
        version = quantarium.__version__
        self.assertRegex(version, r"^\d+\.\d+\.\d+$", "versions are MAJOR.MINOR.PATCH, without pre-release labels")
        pyproject = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
        self.assertEqual(pyproject.group(1), version, "pyproject.toml")
        heading = re.search(r"^## (\S+) — ", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), re.M)
        self.assertEqual(heading.group(1), f"v{version}", "the newest CHANGELOG heading")
        self.assertIn(f"**Version: {version}.**", (ROOT / "README.md").read_text(encoding="utf-8"), "README")


if __name__ == "__main__":
    unittest.main()
