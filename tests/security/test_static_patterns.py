"""Risky code patterns in Quantarium's own code appear only where a reviewed reason says they may.

A new use of any of these fails this test until someone adds it here with
its reason, so it gets a review instead of slipping in.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "quantarium"
APP = PACKAGE / "assets" / "app"

PYTHON_PATTERNS = {
    "exec": re.compile(r"\bexec\("),
    "eval": re.compile(r"\beval\("),
    "dynamic import": re.compile(r"\b__import__\("),
    "pickle": re.compile(r"\bpickle\.(loads?|Unpickler)\b|\bimport pickle\b"),
    "marshal": re.compile(r"\bmarshal\.loads?\("),
    "shell command": re.compile(r"shell\s*=\s*True|\bos\.system\(|\bos\.popen\("),
    "yaml load": re.compile(r"\byaml\.load\("),
}
# Each allowed use: (file relative to the package, pattern name) and why it is acceptable.
ALLOWED = {
    ("engine.py", "exec"): "Loads a strategy file the user wrote or reviewed; unreviewed agent code never reaches it (provenance).",
    ("sandbox_child.py", "exec"): "Runs unreviewed agent code inside the sandbox process, after its policy check and audit hook.",
    ("sandbox_child.py", "dynamic import"): "Imports the sandbox's allowed modules before the audit hook closes imports.",
}
JS_PATTERNS = {
    "raw HTML": re.compile(r"\binnerHTML\b|\bouterHTML\b|insertAdjacentHTML|dangerouslySetInnerHTML|document\.write\("),
    "code from text": re.compile(r"\bnew Function\(|\beval\("),
}


class StaticPatternTests(unittest.TestCase):
    def test_risky_python_appears_only_where_reviewed(self) -> None:
        found = set()
        for path in PACKAGE.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for name, pattern in PYTHON_PATTERNS.items():
                if pattern.search(text):
                    found.add((path.relative_to(PACKAGE).as_posix(), name))
        self.assertEqual(sorted(found - ALLOWED.keys()), [], "new risky code needs a review and an entry in ALLOWED")
        self.assertEqual(sorted(ALLOWED.keys() - found), [], "an ALLOWED entry no longer matches any code; remove it")

    def test_pages_never_render_text_as_markup_or_code(self) -> None:
        offenders = []
        for path in APP.rglob("*.js"):
            if "vendor" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            offenders += [f"{path.relative_to(APP).as_posix()}: {name}" for name, pattern in JS_PATTERNS.items()
                          if pattern.search(text)]
        self.assertEqual(offenders, [])

    def test_links_from_data_stay_inside_the_app(self) -> None:
        # A link built from model, agent, or file text could carry a javascript: URL. Links are
        # in-app routes (#/...); the one exception is a bundled preset's documentation link.
        allowed = {("pages/agents.js", "href=${preset.docs}")}
        found = set()
        for path in APP.rglob("*.js"):
            if "vendor" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"(?:href|src)=\$\{(?!`#/)[^}]*\}", text):
                found.add((path.relative_to(APP / "js").as_posix(), match.group(0)))
        self.assertEqual(sorted(found - allowed), [])


if __name__ == "__main__":
    unittest.main()
