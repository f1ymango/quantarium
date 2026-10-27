"""Which script versions an agent wrote, keyed by content hash.

A script version an agent wrote runs only after the user reviews it in the
app. The strategy loader calls ``refuse_unreviewed`` before executing any
file, so the rule holds however the run was started. A user edit changes the
hash, and the edited file runs as the user's own code. Only the browser app
can mark a version reviewed; agent tokens cannot.
"""

from __future__ import annotations

import hashlib
import importlib.machinery
import json
import os
import posixpath
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from .errors import QuantariumError

REGISTRY = ".quantarium/script-provenance.json"
_LOCK = threading.Lock()


class UnreviewedScript(QuantariumError):
    def __init__(self, message: str) -> None:
        super().__init__(message, "script_unreviewed",
                         "Static simulations run unreviewed agent scripts in the sandbox; Feed and Live "
                         "need the user to review the script on the Agents page first")


def same_key(first: str, second: str) -> bool:
    """Whether two workspace keys name the same file, however they are spelled."""
    def normal(key: str) -> str:
        key = posixpath.normpath(key.replace("\\", "/")).lstrip("/")
        return key.casefold() if os.name == "nt" else key
    return normal(first) == normal(second)


def _keys(entry: dict) -> list[str]:
    return entry.get("keys") or ([entry["key"]] if entry.get("key") else [])


def _registry_for(path: Path) -> Path | None:
    """The provenance file of the workspace a script belongs to."""
    for folder in path.resolve().parents:
        candidate = folder / REGISTRY
        if candidate.is_file():
            return candidate
    return None


def _load(registry: Path) -> dict:
    try:
        data = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # An unreadable registry must not let agent code through.
        raise UnreviewedScript(f"Script provenance is unreadable: {registry}")
    return data if isinstance(data, dict) else {}


def record_agent_write(workspace_root: Path, key: str, data: bytes, connection: dict) -> None:
    with _LOCK:
        _record(workspace_root, key, data, connection)


def _record(workspace_root: Path, key: str, data: bytes, connection: dict) -> None:
    registry = workspace_root / REGISTRY
    entries = _load(registry) if registry.is_file() else {}
    digest = hashlib.sha256(data).hexdigest()
    previous = entries.get(digest) if isinstance(entries.get(digest), dict) else {}
    keys = sorted({*previous.get("keys", [previous["key"]] if previous.get("key") else []), key})
    entries[digest] = {
        "key": key, "keys": keys, "state": "agent-unreviewed", "connection_id": connection.get("id"),
        "connection_name": connection.get("name"),
        "written_utc": datetime.now(timezone.utc).isoformat()}
    registry.parent.mkdir(parents=True, exist_ok=True)
    temporary = registry.with_suffix(".tmp")
    temporary.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, registry)


def entries(workspace_root: Path) -> list[dict]:
    """Agent-written script versions, newest first, and whether each is still on disk."""
    registry = workspace_root / REGISTRY
    if not registry.is_file():
        return []
    listed = []
    for digest, entry in _load(registry).items():
        if not isinstance(entry, dict):
            continue
        path = workspace_root / str(entry.get("key", ""))
        current = path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest
        listed.append({**entry, "sha256": digest, "current": current})
    return sorted(listed, key=lambda item: item.get("written_utc") or "", reverse=True)


def mark_reviewed(workspace_root: Path, digest: object) -> dict:
    """Record that the user read this exact version and allows it to run."""
    with _LOCK:
        return _mark(workspace_root, digest)


def _mark(workspace_root: Path, digest: object) -> dict:
    registry = workspace_root / REGISTRY
    data = _load(registry) if registry.is_file() else {}
    entry = data.get(digest) if isinstance(digest, str) else None
    if not isinstance(entry, dict):
        raise QuantariumError(f"No agent-written script version {digest}", "not_found")
    entry.update({"state": "reviewed", "reviewed_utc": datetime.now(timezone.utc).isoformat()})
    temporary = registry.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, registry)
    return {**entry, "sha256": digest}


def is_unreviewed(workspace_root: Path, data: bytes, key: str | None = None) -> bool:
    registry = workspace_root / REGISTRY
    if not registry.is_file():
        return False
    entry = _load(registry).get(hashlib.sha256(data).hexdigest())
    if not isinstance(entry, dict) or entry.get("state") == "reviewed":
        return False
    return key is None or any(same_key(key, listed) for listed in _keys(entry))


def unreviewed_entry(path: Path, data: bytes) -> dict | None:
    """The provenance record when an agent wrote this content at this path and it is unreviewed.

    Matching the path as well as the content means an agent that writes a copy
    of the user's own script cannot make the user's original look unreviewed.
    """
    registry = _registry_for(path)
    if registry is None:
        return None
    entry = _load(registry).get(hashlib.sha256(data).hexdigest())
    if not isinstance(entry, dict) or entry.get("state") == "reviewed":
        return None
    try:
        relative = path.resolve().relative_to(registry.parent.parent.resolve()).as_posix()
    except ValueError:
        return entry  # outside its workspace: refuse rather than guess
    return entry if any(same_key(relative, listed) for listed in _keys(entry)) else None


def refuse_unreviewed(path: Path, data: bytes) -> None:
    """Raise if this exact file content was written by an agent and not reviewed."""
    entry = unreviewed_entry(path, data)
    if entry is not None:
        raise UnreviewedScript(f"{path.name} was written by {entry.get('connection_name') or 'an agent'} "
                               "and has not been reviewed")


class _ImportGuard:
    """A meta path finder that refuses to import unreviewed agent-written files.

    The strategy loader checks the script it is asked to run; this guard
    covers everything that script imports in turn. It finds the module the
    way Python would, checks the file, and then lets the normal import go on.
    Python's own library and Quantarium's package are not workspace scripts and
    are skipped.
    """

    def __init__(self) -> None:
        self.trusted = {Path(folder).resolve() for folder in
                        (sys.prefix, sys.base_prefix, sys.exec_prefix, Path(__file__).parent)}

    def find_spec(self, name, path=None, target=None):
        spec = importlib.machinery.PathFinder.find_spec(name, path, target)
        origin = getattr(spec, "origin", None)
        if spec is None or not spec.has_location or not isinstance(origin, str) or not origin.endswith(".py"):
            return None
        file = Path(origin)
        resolved = file.resolve()
        if any(resolved.is_relative_to(folder) for folder in self.trusted):
            return None
        try:
            data = file.read_bytes()
        except OSError:
            return None
        refuse_unreviewed(file, data)
        return None


def install_import_guard() -> None:
    """Install the import guard once, ahead of Python's own finders."""
    if not any(isinstance(finder, _ImportGuard) for finder in sys.meta_path):
        sys.meta_path.insert(0, _ImportGuard())
