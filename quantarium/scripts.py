"""Find strategy classes in workspace Python files without importing them.

Files are parsed, never executed, so listing scripts is safe even for code
the user has not reviewed. A strategy is a top-level class with an
``on_bar`` method. Constructor parameters and literal defaults become the
fields the frontend offers.
"""

from __future__ import annotations

import ast
from pathlib import Path


def _parameters(node: ast.ClassDef) -> list[dict]:
    init = next((item for item in node.body
                 if isinstance(item, ast.FunctionDef) and item.name == "__init__"), None)
    if init is None:
        return []
    arguments = init.args.args[1:]
    defaults = [None] * (len(arguments) - len(init.args.defaults)) + list(init.args.defaults)
    result = []
    for argument, default in zip(arguments, defaults):
        entry = {"name": argument.arg, "required": default is None}
        if default is not None:
            try:
                entry["default"] = ast.literal_eval(default)
            except ValueError:
                entry["default"] = None
                entry["default_source"] = ast.unparse(default)
        if argument.annotation is not None:
            entry["annotation"] = ast.unparse(argument.annotation)
        result.append(entry)
    return result


def strategy_classes(path: Path) -> list[dict]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []
    return classes_in_tree(tree)


def classes_in_tree(tree: ast.Module) -> list[dict]:
    """Strategy classes in a parsed module: top-level classes with ``on_bar``."""
    classes = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        methods = {item.name for item in node.body if isinstance(item, ast.FunctionDef)}
        if "on_bar" not in methods:
            continue
        doc = ast.get_docstring(node) or ""
        classes.append({
            "name": node.name, "doc": doc.strip().splitlines()[0] if doc.strip() else "",
            "parameters": _parameters(node),
            "stateful": {"snapshot", "restore"} <= methods,
        })
    return classes


def find_class(path: Path, name: str | None) -> dict | None:
    return next((item for item in strategy_classes(path) if item["name"] == name), None)


def allocator_classes(path: Path) -> list[dict]:
    """Allocator classes in a file: top-level classes with an ``allocate`` method."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []
    found = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and any(isinstance(item, ast.FunctionDef) and item.name == "allocate"
                                                  for item in node.body):
            doc = ast.get_docstring(node) or ""
            found.append({"name": node.name, "doc": doc.strip().splitlines()[0] if doc.strip() else "",
                          "parameters": _parameters(node)})
    return found


def find_allocator(path: Path, name: str | None) -> dict | None:
    return next((item for item in allocator_classes(path) if item["name"] == name), None)
