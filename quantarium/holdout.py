"""Holdout scenarios: markets the user keeps out of agents' sight.

An agent that tries many variants will find one that fits the scenarios it
can see. Holdout scenarios are the test set: agents cannot read their
prices, run on them, or see results from them. Quantarium recognizes holdout
results by content (a run's price-data hash or scenario manifest hash), not
by name, so copying or renaming a scenario does not reveal it. An agent may
ask for one holdout test of each cell version, through a proposal the user
approves; its results stay visible only to the user.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .data import load_daily_csv
from .errors import QuantariumError
from .scenario import load_scenario

STORE = ".quantarium/holdout.json"


class Holdout:
    def __init__(self, workspace) -> None:
        self.workspace = workspace
        self.path = workspace.root / STORE
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, dict]] = {}

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"scenarios": [], "tests": []}
        if not isinstance(data, dict):
            return {"scenarios": [], "tests": []}
        return {"scenarios": [key for key in data.get("scenarios", []) if isinstance(key, str)],
                "tests": [item for item in data.get("tests", []) if isinstance(item, dict)]}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)

    def keys(self) -> list[str]:
        return self._read()["scenarios"]

    def set(self, key: object, holdout: object) -> list[str]:
        if not isinstance(key, str):
            raise QuantariumError("key must be a scenario folder", "invalid_parameter")
        directory = self.workspace.resolve(key)
        load_scenario(directory)
        normalized = self.workspace.key(directory)
        with self._lock:
            data = self._read()
            keys = [item for item in data["scenarios"] if item != normalized]
            if holdout:
                keys.append(normalized)
            data["scenarios"] = sorted(keys)
            self._write(data)
        return data["scenarios"]

    def _fingerprints(self, key: str) -> dict | None:
        """A holdout scenario's directory and content hashes, cached by modification time."""
        try:
            directory = self.workspace.resolve(key)
            scenario = load_scenario(directory)
            stamp = scenario.prices_path.stat().st_mtime
        except (OSError, ValueError, PermissionError):
            return None
        cached = self._cache.get(key)
        if cached and cached[0] == stamp:
            return cached[1]
        _, _, data_hash = load_daily_csv(scenario.prices_path, set(scenario.symbols))
        info = {"directory": directory, "name": scenario.name, "manifest": scenario.manifest_sha256,
                "data": data_hash}
        self._cache[key] = (stamp, info)
        return info

    def scenarios(self) -> list[dict]:
        return [info for info in (self._fingerprints(key) for key in self.keys()) if info]

    def covers(self, path: Path) -> bool:
        """Whether a path is a holdout scenario or inside one."""
        resolved = path.resolve()
        return any(info["directory"] == resolved or info["directory"] in resolved.parents
                   for info in self.scenarios())

    def manifests(self) -> set[str]:
        return {info["manifest"] for info in self.scenarios()}

    def run_is_holdout(self, database: Path, run_id: str | None) -> bool:
        """Whether a Static run (the latest completed one without an id) used holdout data."""
        holdouts = self.scenarios()
        if not holdouts or not database.is_file():
            return False
        with closing(sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(runs)")}
            if "data_sha256" not in columns:
                return False
            metadata = "metadata_json" if "metadata_json" in columns else "NULL"
            if run_id:
                rows = db.execute(f"SELECT data_sha256, {metadata} FROM runs WHERE id=?", (run_id,)).fetchall()
            else:
                rows = db.execute(f"SELECT data_sha256, {metadata} FROM runs WHERE status='completed' "
                                  "ORDER BY created_utc DESC LIMIT 1").fetchall()
        hashes = {info["data"] for info in holdouts}
        manifests = {info["manifest"] for info in holdouts}
        for data_hash, metadata_json in rows:
            if data_hash in hashes:
                return True
            try:
                if json.loads(metadata_json or "{}").get("scenario", {}).get("manifest_sha256") in manifests:
                    return True
            except ValueError:
                pass
        return False

    # Holdout tests --------------------------------------------------------------

    def tested(self, fingerprint: str) -> bool:
        return any(item.get("fingerprint") == fingerprint for item in self._read()["tests"])

    def record_test(self, fingerprints: dict[str, str], proposal_id: str) -> None:
        with self._lock:
            data = self._read()
            now = datetime.now(timezone.utc).isoformat()
            data["tests"] += [{"cell_id": cell_id, "fingerprint": fingerprint, "proposal_id": proposal_id,
                               "utc": now} for cell_id, fingerprint in fingerprints.items()]
            self._write(data)
