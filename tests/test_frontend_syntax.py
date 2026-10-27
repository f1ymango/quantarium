"""The app's JavaScript must at least have balanced brackets.

No test runs the pages, and one unbalanced parenthesis blanks every page
that imports the file (it happened to the cell page in v0.4.1). This scanner
understands strings, template literals with nested ``${}``, comments, and
regular-expression literals well enough to check the app's own files; it is
not a full JavaScript parser.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "quantarium" / "assets" / "app" / "js"
PAIRS = {")": "(", "]": "[", "}": "{"}
# After these characters (or at the start), a slash begins a regular expression.
REGEX_BEFORE = set("(,=:[!&|?{};+-*%<>~^") | {""}


def problems(source: str) -> list[str]:
    stack: list[tuple[str, int]] = []  # ("(", line), or ("`", line) for a template, ("${", line) inside one
    line, index, length = 1, 0, len(source)
    previous = ""  # the last significant character outside strings and comments

    def fail(message: str) -> list[str]:
        return [f"line {line}: {message}"]

    while index < length:
        char = source[index]
        if stack and stack[-1][0] == "`":
            # Inside a template literal: only backslashes, ${, and the closing backtick matter.
            if char == "\\":
                index += 2
                continue
            if char == "`":
                stack.pop()
                previous = "`"
            elif source.startswith("${", index):
                stack.append(("${", line))
                index += 1
            elif char == "\n":
                line += 1
            index += 1
            continue
        if char == "\n":
            line += 1
        elif source.startswith("//", index):
            end = source.find("\n", index)
            index = length if end < 0 else end
            continue
        elif source.startswith("/*", index):
            end = source.find("*/", index + 2)
            if end < 0:
                return fail("unclosed comment")
            line += source.count("\n", index, end)
            index = end + 2
            continue
        elif char in "'\"":
            end = index + 1
            while end < length and source[end] != char:
                if source[end] == "\\":
                    end += 1
                elif source[end] == "\n":
                    return fail("unclosed string")
                end += 1
            index = end + 1
            previous = char
            continue
        elif char == "`":
            stack.append(("`", line))
        elif char == "/" and previous in REGEX_BEFORE:
            end, in_class = index + 1, False
            while end < length and (in_class or source[end] != "/"):
                if source[end] == "\\":
                    end += 1
                elif source[end] == "[":
                    in_class = True
                elif source[end] == "]":
                    in_class = False
                elif source[end] == "\n":
                    return fail("unclosed regular expression")
                end += 1
            index = end + 1
            previous = "/"
            continue
        elif char in "([{":
            stack.append((char, line))
        elif char in ")]}":
            if not stack:
                return fail(f"unexpected {char}")
            opener, opened = stack.pop()
            if char == "}" and opener == "${":
                pass  # back inside the enclosing template literal
            elif PAIRS[char] != opener:
                return fail(f"{char} closes {opener} from line {opened}")
        if not char.isspace():
            previous = char if not (char.isalnum() or char in "_$") else "a"
        index += 1
    if stack:
        opener, opened = stack[-1]
        return [f"line {opened}: {opener} is never closed"]
    return []


class FrontendSyntaxTests(unittest.TestCase):
    def test_every_script_has_balanced_brackets(self) -> None:
        files = sorted(APP.rglob("*.js"))
        self.assertTrue(files)
        for path in files:
            with self.subTest(script=path.relative_to(ROOT).as_posix()):
                self.assertEqual(problems(path.read_text(encoding="utf-8")), [])

    def test_the_scanner_catches_what_it_should(self) -> None:
        self.assertEqual(problems("const a = f(g(1), [2, {b: 3}]);\nconst t = html`<p>${x ? `y ${z}` : '('}</p>`;\n"), [])
        self.assertEqual(problems("const r = text.split(/\\n\\s*\\n/); const s = /\"([^\"]*)\"|(\\S+)/g;"), [])
        self.assertEqual(problems("// a ( in a comment\n/* and ] here */ const s = '{';"), [])
        self.assertTrue(problems("update({ x: f(g(1) });"))  # the v0.4.1 cell page bug
        self.assertTrue(problems("const t = html`<p>${x</p>`;"))
        self.assertTrue(problems("const s = 'unclosed;\n"))


if __name__ == "__main__":
    unittest.main()
