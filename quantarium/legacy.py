"""Barloom, the project's name before 1.0: what old workspaces, scripts, and files still use.

Quantarium reads everything Barloom wrote. New files use the new names; old
ones keep working:

- a workspace's ``.barloom`` folder becomes ``.quantarium`` the first time
  Quantarium opens it, and saved settings that still name ``.barloom/...``
  resolve to the new folder;
- ``barloom-workspace.json``, ``BARLOOM_*`` environment variables, ``blm_``
  tokens (checked by their hash, like any token), keys saved in the keychain
  under ``barloom``, and ``barloom-*-v1`` files are all read;
- scripts and saved entrypoints that import ``barloom.*`` load the same
  ``quantarium.*`` modules, through the ``barloom`` alias package.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

NAME, LEGACY_NAME = "quantarium", "barloom"
HIDDEN, LEGACY_HIDDEN = ".quantarium", ".barloom"
WORKSPACE_FILES = ("quantarium-workspace.json", "barloom-workspace.json")
# Only a whole ``barloom.module:Class`` entrypoint is renamed; other text that starts with "barloom." is data.
_LEGACY_ENTRYPOINT = re.compile(r"barloom(?:\.[A-Za-z_]\w*)+:[A-Za-z_]\w*")


def env(name: str, default: str | None = None) -> str | None:
    """``QUANTARIUM_<name>``, or else the older ``BARLOOM_<name>``."""
    return os.environ.get(f"QUANTARIUM_{name}") or os.environ.get(f"BARLOOM_{name}") or default


def is_format(value: object, kind: str) -> bool:
    """Whether a file's ``format`` names ``kind`` under its current or its Barloom name.

    The value comes from an untrusted file, so anything that is not a string is simply not the format.
    """
    return isinstance(value, str) and value in {f"quantarium-{kind}-v1", f"barloom-{kind}-v1"}


def current_entrypoint(entrypoint: object) -> object:
    """``barloom.referee:ReplayDecisions`` as ``quantarium.referee:ReplayDecisions``; anything else unchanged."""
    if isinstance(entrypoint, str) and _LEGACY_ENTRYPOINT.fullmatch(entrypoint):
        return NAME + entrypoint[len(LEGACY_NAME):]
    return entrypoint


def current_relative(relative: str) -> str:
    """A saved workspace path inside ``.barloom/`` as the same path inside ``.quantarium/``."""
    normalized = relative.replace("\\", "/")
    if normalized == LEGACY_HIDDEN or normalized.startswith(LEGACY_HIDDEN + "/"):
        return HIDDEN + normalized[len(LEGACY_HIDDEN):]
    return relative


def migrate_hidden(root: Path) -> None:
    """Rename an old workspace's ``.barloom`` folder to ``.quantarium``, once.

    Nothing is copied or merged: when ``.quantarium`` already exists the old
    folder is left alone. A folder still open in another program cannot be
    renamed on Windows; the error says so rather than starting with empty
    settings.
    """
    old, new = root / LEGACY_HIDDEN, root / HIDDEN
    if old.is_dir() and not new.exists():
        try:
            old.rename(new)
        except OSError as exc:
            raise RuntimeError(f"Could not rename {old} to {new}: {exc}. Close any Barloom server or program "
                               f"using this workspace, then start again") from exc


def current_value(value: object) -> object:
    """A saved record with Barloom's entrypoints and hidden-folder paths in their Quantarium form.

    Used to compare or run what was saved before 1.0, never to rewrite it:
    stored records keep their bytes, so their fingerprints and reports still verify.
    """
    if isinstance(value, dict):
        return {key: current_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [current_value(item) for item in value]
    if isinstance(value, str):
        value = current_entrypoint(value)
        for sep in ("/", "\\"):
            value = value.replace(f"{sep}{LEGACY_HIDDEN}{sep}", f"{sep}{HIDDEN}{sep}")
        return current_relative(value)
    return value
