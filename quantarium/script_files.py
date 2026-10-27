"""Read, write, and check strategy scripts through the API.

Writes are limited to Python files inside the workspace's script folders,
never under a hidden folder such as ``.quantarium/``. Overwriting needs the
SHA-256 of the version the writer last read (``base_sha256``), so a write
never silently replaces an edit someone else saved in the meantime. Checking
parses the source without running it.
"""

from __future__ import annotations

import ast
import hashlib
import os
from pathlib import Path

from .errors import QuantariumError, NotFound
from .provenance import record_agent_write
from .scripts import classes_in_tree

MAX_SCRIPT_BYTES = 200_000


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_source(source: str, filename: str = "<script>") -> dict:
    """Parse without executing; report strategy classes or the problems found."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        return {"ok": False, "classes": [],
                "problems": [f"Syntax error on line {exc.lineno}: {exc.msg}"]}
    from .sandbox import policy_problems

    classes = classes_in_tree(tree)
    problems = [] if classes else ["No strategy class found: define a top-level class with an on_bar method"]
    # What would stop this script running in the sandbox, where unreviewed agent code runs.
    return {"ok": not problems, "classes": classes, "problems": problems,
            "sandbox_problems": policy_problems(source, filename)}


class ScriptFiles:
    def __init__(self, workspace) -> None:
        self.workspace = workspace

    def path(self, key: object) -> Path:
        """The file for a script key, refused outside the script folders."""
        if not isinstance(key, str) or not key.endswith(".py") or "\\" in key:
            raise QuantariumError("A script key is a workspace path ending in .py, with forward slashes",
                               "invalid_script_path")
        path = self.workspace.resolve(key)
        relative = path.relative_to(self.workspace.root)
        if any(part.startswith(".") for part in relative.parts):
            raise PermissionError(f"Scripts cannot live in hidden folders: {key}")
        folders = [self.workspace.resolve(folder) for folder in self.workspace.folders["scripts"]]
        if not any(folder in path.parents for folder in folders):
            raise QuantariumError(f"{key} is outside the script folders",
                               "invalid_script_path",
                               f"Use a path under {', '.join(self.workspace.folders['scripts'])}")
        return path

    def read(self, key: object) -> dict:
        path = self.path(key)
        if not path.is_file():
            raise NotFound(f"Unknown script: {key}", "script_not_found", "GET scripts lists them")
        data = path.read_bytes()
        source = data.decode("utf-8")
        return {"key": key, "source": source, "sha256": sha256(data),
                "check": check_source(source, str(key))}

    def write(self, key: object, source: object, base_sha256: object = None,
              agent: dict | None = None) -> dict:
        """Save a script; ``agent`` is the connection when an agent writes it."""
        path = self.path(key)
        if agent is not None and path.name == "__init__.py":
            raise QuantariumError("Agents cannot write package files", "invalid_script_path")
        if not isinstance(source, str):
            raise QuantariumError("source must be a string", "invalid_parameter")
        data = source.encode("utf-8")
        if len(data) > MAX_SCRIPT_BYTES:
            raise QuantariumError(f"Scripts are limited to {MAX_SCRIPT_BYTES} bytes", "script_too_large")
        if path.exists():
            current = sha256(path.read_bytes())
            if base_sha256 != current:
                raise QuantariumError(f"{key} changed since it was read", "script_changed",
                                   f"Read it again with GET scripts/{key}, merge, and pass its sha256")
        elif base_sha256 is not None:
            raise QuantariumError(f"{key} does not exist; omit base_sha256 to create it", "script_not_found")
        path.parent.mkdir(parents=True, exist_ok=True)
        if agent is not None:
            # Recorded before the file exists, so no moment has it unmarked.
            # Under the file's canonical key, so another spelling of the path cannot miss it.
            record_agent_write(self.workspace.root, self.workspace.key(path), data, agent)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_bytes(data)
        os.replace(temporary, path)
        return {"key": key, "sha256": sha256(data), "check": check_source(source, str(key))}
