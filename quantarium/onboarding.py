"""The first-run tutorial as a state of the workspace.

While the tutorial is active, the app works in a private, initially empty
workspace at ``.quantarium/tutorial/``, so the user's own cells, scenarios, and
records are out of sight and untouched. Finishing promotes the tutorial cell,
market, configs, and Static/Feed results into the real workspace at the same
relative paths, so its Feed experiment keeps working. Lab practice is discarded
with the private workspace; its ledger and database are not promoted.
Skipping or abandoning it discards the private workspace, leaving the real one
exactly as it was. Progress lives in ``.quantarium/onboarding.json``.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from datetime import datetime, timezone
from importlib import resources
from typing import Callable

STATE_FILE = ".quantarium/onboarding.json"
TUTORIAL_ROOT = ".quantarium/tutorial"
STARTER_FILE = "strategies/starter.py"
STATUSES = ("pending", "active", "completed", "skipped")
# Each run's names avoid everything in the real workspace, so finishing never
# overwrites a file. The script keeps its usual name unless the user has
# edited their copy of the starter strategies.
BASES = {"scenario": "first_market", "cell": "My first cell", "feed": "first_forward",
         "script": "strategies/starter", "database": "runs/tutorial"}


class TutorialNotActive(ValueError):
    """A tutorial-scoped request arrived while no tutorial is running."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _starter_bytes() -> bytes:
    return resources.files("quantarium").joinpath("assets", "starter", "strategies.py").read_bytes()


def _slug(text: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in text.lower()).strip("-")


class Onboarding:
    def __init__(self, workspace, library, api_factory: Callable) -> None:
        self.workspace = workspace
        self.library = library
        self.api_factory = api_factory
        self.path = workspace.root / STATE_FILE
        self.tutorial_root = workspace.root / TUTORIAL_ROOT
        self._api = None
        self._lock = threading.RLock()

    # State file ---------------------------------------------------------------

    def _read(self) -> dict | None:
        if not self.path.is_file():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("status") not in STATUSES:
            return None
        if data["status"] == "active" and not self.tutorial_root.is_dir():
            # A run from v0.2.1, which worked in the real workspace, or one
            # whose private folder was removed: offer a fresh start instead.
            return {"status": "pending", "step": 0, "cell_id": None, "names": None}
        return data

    def _write(self, data: dict) -> dict:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data["updated_utc"] = _now()
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)
        return data

    def state(self) -> dict:
        data = self._read() or {"status": "pending", "step": 0, "cell_id": None, "names": None}
        starter = (self.workspace.root / STARTER_FILE).is_file()
        return {**data, "starter_script": STARTER_FILE if starter else None}

    def active(self) -> bool:
        data = self._read()
        return bool(data) and data["status"] == "active"

    def seed_starter(self) -> str | None:
        """Copy the starter strategies into the real workspace once; never overwrite."""
        target = self.workspace.root / STARTER_FILE
        if target.exists():
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_starter_bytes())
        return STARTER_FILE

    # The private tutorial workspace ---------------------------------------------

    def tutorial_api(self):
        """The Api serving the tutorial state; created on first use after a restart."""
        with self._lock:
            data = self._read()
            if not data or data["status"] != "active" or not self.tutorial_root.is_dir():
                raise TutorialNotActive("No tutorial is running")
            if self._api is None:
                from .workspace import Workspace
                self._api = self.api_factory(Workspace(self.tutorial_root))
                # Results go to databases named for this run, so promoting
                # them never meets an existing file.
                self._api.deployer.suite_database = data["names"]["database"]
                self._api.deployer.static_database = data["names"]["database"]
            return self._api

    def _discard(self) -> None:
        """Stop the tutorial's jobs and delete its private workspace."""
        if self._api is not None:
            self._api.close(timeout=None)
            self._api = None
        if self.tutorial_root.exists():
            root = self.tutorial_root.resolve()
            if root.parent != (self.workspace.root / ".quantarium").resolve() or root.name != "tutorial":
                raise RuntimeError("Unexpected tutorial folder location")
            shutil.rmtree(root)

    def _unique_names(self) -> dict:
        """Names that cannot collide with anything already in the real workspace."""
        root = self.workspace.root
        cells = {record["name"].casefold() for record in self.library.all()}
        scenarios = {item["name"] for item in self.workspace.scenarios() if "name" in item}
        experiments = {experiment["id"] for database in self.workspace.databases()
                       for experiment in database["experiments"] if experiment["kind"] == "feed"}
        starter = _starter_bytes()

        def taken(key: str, candidate: str) -> bool:
            if key == "cell":
                return candidate.casefold() in cells
            if key == "scenario":
                return candidate in scenarios or (root / "scenarios" / candidate).exists()
            if key == "feed":
                slug = _slug(candidate)
                return (candidate in experiments
                        or (root / "configs" / "generated" / f"feed-{slug}.json").exists()
                        or (root / "runs" / f"feed-{slug}.sqlite").exists())
            if key == "script":
                path = root / f"{candidate}.py"
                return path.exists() and path.read_bytes() != starter
            return (root / f"{candidate}.sqlite").exists()

        names = {}
        for key, base in BASES.items():
            number = 1
            while True:
                candidate = base if number == 1 else (
                    f"{base} {number}" if key == "cell" else f"{base}_{number}")
                if not taken(key, candidate):
                    break
                number += 1
            names[key] = (f"{candidate}.py" if key == "script"
                          else f"{candidate}.sqlite" if key == "database" else candidate)
        return names

    # Transitions ------------------------------------------------------------------

    def start(self) -> dict:
        """Enter the tutorial state with a fresh, empty private workspace."""
        with self._lock:
            self._discard()
            names = self._unique_names()
            script = self.tutorial_root / names["script"]
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_bytes(_starter_bytes())
            return self._write({"status": "active", "step": 0, "cell_id": None,
                                "names": names, "started_utc": _now()})

    def step(self, step: int, cell_id: str | None = None) -> dict:
        with self._lock:
            data = self._read()
            if not data or data["status"] != "active":
                raise ValueError("The tutorial is not running")
            if isinstance(step, bool) or not isinstance(step, int) or step < 0:
                raise ValueError("step must be a nonnegative integer")
            data["step"] = step
            if cell_id is not None:
                self.tutorial_api().cells.get(cell_id)
                data["cell_id"] = cell_id
            return self._write(data)

    def finish(self, status: str) -> dict:
        """Leave the tutorial: completing keeps what it made, skipping discards it."""
        if status not in {"completed", "skipped"}:
            raise ValueError("status must be completed or skipped")
        with self._lock:
            data = self._read() or {"step": 0, "cell_id": None, "names": None}
            if status == "completed":
                if data.get("status") != "active" or not data.get("cell_id"):
                    raise ValueError("Only a running tutorial with a cell can be completed")
                if self._api is not None:
                    self._api.close(timeout=None)
                    self._api = None
                self._promote()
            self._discard()
            self.seed_starter()
            data.update({"status": status, "finished_utc": _now()})
            return self._write(data)

    def _promote(self) -> None:
        """Promote cell practice, while keeping its Lab evidence in the private workspace."""
        copies = []
        for source in sorted(self.tutorial_root.rglob("*")):
            relative = source.relative_to(self.tutorial_root)
            if (not source.is_file() or relative.parts[0] == ".quantarium"
                    or relative.as_posix() == "runs/lab.sqlite"):
                continue
            target = self.workspace.root / relative
            if target.exists() and target.read_bytes() != source.read_bytes():
                raise ValueError(f"Cannot finish: {relative.as_posix()} already exists")
            copies.append((source, target))
        for source, target in copies:
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(source, target)

    def close(self) -> None:
        with self._lock:
            if self._api is not None:
                self._api.close()
                self._api = None
