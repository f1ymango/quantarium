"""The logo the app serves is a plain picture (v1.0.7)."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOGOS = (ROOT / "quantarium" / "assets" / "app" / "logo.svg", ROOT / "docs" / "images" / "logo.svg")


class BrandingTests(unittest.TestCase):
    def test_the_logo_is_a_plain_picture(self) -> None:
        for path in LOGOS:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertTrue(text.lstrip().startswith("<svg"))
                self.assertNotRegex(text, r"(?i)<script|\bon[a-z]+\s*=|href\s*=|<foreignObject|url\(")
        self.assertEqual(LOGOS[0].read_text(encoding="utf-8"), LOGOS[1].read_text(encoding="utf-8"))

    def test_documents_and_the_app_use_one_name(self) -> None:
        # The pre-1.0 name lives only in the compatibility code that reads older workspaces.
        skip = {"runs", "build", "dist", "node_modules", "__pycache__"}
        found = []
        for path in [*ROOT.rglob("*.md"), *(ROOT / "quantarium" / "assets").rglob("*")]:
            parts = set(path.relative_to(ROOT).parts)
            if not path.is_file() or parts & skip or any(part.startswith(".") for part in parts):
                continue
            if path.suffix in {".md", ".js", ".html", ".css", ".json", ".svg"} and                     "barloom" in path.read_text(encoding="utf-8", errors="ignore").lower():
                found.append(str(path.relative_to(ROOT)))
        self.assertEqual(found, [])

    def test_the_app_uses_it(self) -> None:
        app = ROOT / "quantarium" / "assets" / "app"
        self.assertIn('rel="icon" type="image/svg+xml" href="/logo.svg"', (app / "index.html").read_text(encoding="utf-8"))
        self.assertIn('src="/logo.svg"', (app / "js" / "main.js").read_text(encoding="utf-8"))
        self.assertIn('src="/logo.svg"', (app / "js" / "tutorial" / "tour.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
