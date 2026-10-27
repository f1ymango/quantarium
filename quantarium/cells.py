"""A workspace library of cell definitions that exist before any deployment.

Each cell is one JSON file, ``cells/{id}.json``. A cell may be an incomplete
draft; it becomes deployable once its specification passes the same checks
the engine applies to configs. Saving a cell whose current version has been
deployed creates a new version, so earlier results keep describing the
settings that produced them.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

from .legacy import is_format
from .config import _cells
from .errors import QuantariumError, NotFound
from .scripts import find_allocator, find_class

BUILTIN_ALLOCATORS = ("FixedAllocation", "TrendRegime")

FORMAT = "quantarium-cell-v1"
FOLDER = "cells"
DEFAULT_NAME = "Unnamed cell"
MAX_NAME = 200
_ID = re.compile(r"c_[a-f0-9]{10}")
RULE_DEFAULTS = {
    "rebalance_every_sessions": 1, "max_position_weight": 1.0,
    "min_cash_weight": 0.0, "max_previous_volume_participation": None,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def blank_spec() -> dict:
    return {"mode": "simple", "symbols": [], "initial_cash": 10000, "sleeves": [],
            "initial_positions": {}, "information_end": None, "model_card": None, "ask": None,
            "allocator": None,
            **RULE_DEFAULTS}


class CellLibrary:
    def __init__(self, workspace) -> None:
        self.workspace = workspace
        self.folder = workspace.root / FOLDER

    # Storage -------------------------------------------------------------

    def _path(self, cell_id: str) -> Path:
        if not _ID.fullmatch(cell_id):
            raise NotFound(f"Unknown cell: {cell_id}", "cell_not_found", "List cells with GET cells")
        return self.folder / f"{cell_id}.json"

    def _write(self, record: dict) -> dict:
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self._path(record["id"])
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
        return record

    def get(self, cell_id: str) -> dict:
        path = self._path(cell_id)
        if not path.is_file():
            raise NotFound(f"Unknown cell: {cell_id}", "cell_not_found", "List cells with GET cells")
        record = json.loads(path.read_text(encoding="utf-8"))
        if not is_format(record.get("format"), "cell"):
            raise ValueError(f"{path.name} is not a Quantarium cell")
        return record

    def all(self, include_archived: bool = True) -> list[dict]:
        if not self.folder.is_dir():
            return []
        records = []
        for path in sorted(self.folder.glob("c_*.json")):
            try:
                record = self.get(path.stem)
            except (ValueError, OSError):
                continue
            if include_archived or not record.get("archived"):
                records.append(record)
        return sorted(records, key=lambda item: item["updated_utc"], reverse=True)

    def _unique_name(self, name: str, ignore: str | None = None) -> str:
        taken = {record["name"].casefold() for record in self.all() if record["id"] != ignore}
        if name.casefold() not in taken:
            return name
        number = 2
        while f"{name} {number}".casefold() in taken:
            number += 1
        return f"{name} {number}"

    # Changes -------------------------------------------------------------

    def create(self, name: str | None = None, spec: dict | None = None) -> dict:
        now = _now()
        record = {
            "format": FORMAT, "id": "c_" + secrets.token_hex(5),
            "name": self._unique_name(_check_name(name) if name else DEFAULT_NAME),
            "archived": False, "created_utc": now, "updated_utc": now, "version": 1,
            "spec": copy.deepcopy(spec) if spec is not None else blank_spec(),
            "history": [], "deployments": [],
        }
        return self._write(record)

    def save(self, cell_id: str, *, name: str | None = None, spec: dict | None = None) -> dict:
        record = self.get(cell_id)
        if name is not None:
            name = _check_name(name)
            if name.casefold() != record["name"].casefold() and self._unique_name(name, cell_id) != name:
                raise QuantariumError(f"Another cell is already named {name}", "name_taken",
                                   "Choose a different name")
        new_spec = record["spec"] if spec is None else _normalize(spec)
        if new_spec == record["spec"] and (name is None or name == record["name"]):
            return record
        deployed = any(item["version"] == record["version"] for item in record["deployments"])
        if deployed and new_spec != record["spec"]:
            record["history"].append({"version": record["version"], "name": record["name"],
                                      "spec": record["spec"], "saved_utc": record["updated_utc"]})
            record["version"] += 1
        record["spec"] = new_spec
        if name is not None:
            record["name"] = name
        record["updated_utc"] = _now()
        return self._write(record)

    def set_archived(self, cell_ids: list[str], archived: bool) -> list[dict]:
        changed = []
        for cell_id in cell_ids:
            record = self.get(cell_id)
            record["archived"] = archived
            record["updated_utc"] = _now()
            changed.append(self._write(record))
        return changed

    def duplicate(self, cell_id: str) -> dict:
        source = self.get(cell_id)
        return self.create(f"{source['name']} copy", source["spec"])

    def delete(self, cell_id: str) -> None:
        record = self.get(cell_id)
        if record["deployments"]:
            raise QuantariumError(f"{record['name']} has results; archive it instead", "cell_has_results",
                               "Archive the cell instead")
        self._path(cell_id).unlink()

    def record_deployment(self, cell_id: str, entry: dict) -> dict:
        record = self.get(cell_id)
        record["deployments"].append({**entry, "version": record["version"],
                                      "created_utc": _now()})
        return self._write(record)

    # Engine view ---------------------------------------------------------

    def check(self, record: dict) -> list[str]:
        """Problems that keep the cell from being deployed; empty when ready."""
        try:
            self.engine_cell(record)
        except (OSError, ValueError, TypeError) as exc:
            return [part for part in str(exc).split("\n") if part]
        return []

    def _decision_problems(self, spec: dict, strategies: list) -> list[str]:
        """Check a cell that replays a decision file or an inbox, and add its built-in strategy."""
        from .decisions import load_decisions
        from .referee import DECIDER_ENTRYPOINT, ENTRYPOINT, INBOX_ENTRYPOINT

        sleeves = spec.get("sleeves") or []
        if len(sleeves) != 1:
            return ["A cell that replays a decision file has that one source and no scripts"]
        if spec.get("allocator") is not None or spec.get("ask") is not None or spec.get("model_card"):
            return ["A cell that replays a decision file has no allocator, model access, or risk card"]
        if spec.get("rebalance_every_sessions", 1) != 1:
            return ["A cell that replays a decision file follows it every session; set the rebalance interval to 1"]
        simple = spec.get("mode") == "simple"
        symbol = spec["symbols"][0] if simple and spec.get("symbols") else None
        if "decider" in sleeves[0]:
            from .deciders import get

            try:
                get(self.workspace.root, sleeves[0]["decider"])
            except (OSError, ValueError) as exc:
                return [str(exc)]
            tapes = self.workspace.resolve(self.workspace.folders["decisions"][0]) / "recorded"
            strategies.append({"entrypoint": DECIDER_ENTRYPOINT, "allocation": 1,
                               "parameters": {"decider": sleeves[0]["decider"], "workspace": str(self.workspace.root),
                                              "tapes": str(tapes), "symbols": list(spec.get("symbols") or []),
                                              "symbol": symbol}})
            return []
        if "inbox" in sleeves[0]:
            from .inbox import _header, inbox_path

            try:
                path = inbox_path(self.workspace.root, sleeves[0]["inbox"])
                listed = _header(path)["symbols"]
            except (OSError, ValueError, KeyError) as exc:
                return [f"Decision inbox {sleeves[0]['inbox']} is missing or invalid ({exc})"]
            outside = set(listed) - set(spec.get("symbols") or [])
            if outside:
                return [f"Decision inbox {sleeves[0]['inbox']} takes symbols the cell does not hold: "
                        f"{', '.join(sorted(outside))}"]
            strategies.append({"entrypoint": INBOX_ENTRYPOINT, "allocation": 1,
                               "parameters": {"inbox": str(path), "symbol": symbol}})
            return []
        key = sleeves[0]["decisions"]
        try:
            path = self.workspace.resolve(key)
            if not any(self.workspace.resolve(folder) in path.parents for folder in self.workspace.folders["decisions"]):
                return [f"Decision files belong in {', '.join(self.workspace.folders['decisions'])}/"]
            loaded = load_decisions(path)
        except (OSError, PermissionError) as exc:
            return [f"Decision file {key} was not found ({type(exc).__name__})"]
        except ValueError as exc:
            return [f"Decision file {key} is invalid: {exc}"]
        outside = set(loaded.symbols) - set(spec.get("symbols") or [])
        if outside:
            return [f"Decision file {key} names symbols the cell does not hold: {', '.join(sorted(outside))}"]
        strategies.append({"entrypoint": ENTRYPOINT, "allocation": 1,
                           "parameters": {"decisions": str(path), "sha256": loaded.sha256, "symbol": symbol}})
        return []

    def engine_cell(self, record: dict) -> tuple[dict, Path]:
        """The config entry the engine accepts, and the folder its scripts load from."""
        spec = record["spec"]
        problems = []
        sleeves = spec.get("sleeves") or []
        if not spec.get("symbols"):
            problems.append("Add at least one holding")
        if not sleeves:
            problems.append("Add a script")
        folders = set()
        strategies = []
        if any(_outside(sleeve) for sleeve in sleeves):
            problems += self._decision_problems(spec, strategies)
            sleeves = []
        for sleeve in sleeves:
            script, name = sleeve.get("script"), sleeve.get("class")
            try:
                path = self.workspace.resolve(script or "")
                if not path.is_file() or find_class(path, name) is None:
                    raise FileNotFoundError
            except (FileNotFoundError, PermissionError, TypeError):
                problems.append(f"Script {script}:{name} was not found")
                continue
            folders.add(path.parent)
            strategies.append({"entrypoint": f"{path.stem}:{name}",
                               "allocation": sleeve.get("allocation", 1),
                               "parameters": sleeve.get("parameters", {})})
        allocator = spec.get("allocator")
        allocator_entry = None
        if allocator is not None:
            if not isinstance(allocator, dict) or not isinstance(allocator.get("class"), str):
                problems.append("The allocator needs a class")
            elif spec.get("mode") != "complex":
                problems.append("An allocator needs a Complex cell")
            elif allocator.get("builtin"):
                if allocator["class"] not in BUILTIN_ALLOCATORS:
                    problems.append(f"Unknown built-in allocator {allocator['class']}")
                else:
                    allocator_entry = {"entrypoint": f"quantarium.allocators:{allocator['class']}",
                                       "parameters": allocator.get("parameters", {})}
            else:
                try:
                    path = self.workspace.resolve(allocator.get("script") or "")
                    if not path.is_file() or find_allocator(path, allocator["class"]) is None:
                        raise FileNotFoundError
                    folders.add(path.parent)
                    allocator_entry = {"entrypoint": f"{path.stem}:{allocator['class']}",
                                       "parameters": allocator.get("parameters", {})}
                except (FileNotFoundError, PermissionError, TypeError):
                    problems.append(f"Allocator {allocator.get('script')}:{allocator['class']} was not found")
        if len(folders) > 1:
            problems.append("All of a cell's scripts must be in one folder")
        if problems:
            raise ValueError("\n".join(problems))
        entry = {"name": record["name"], "mode": spec["mode"], "symbols": spec["symbols"],
                 "initial_cash": spec["initial_cash"], "strategies": strategies,
                 **{key: spec.get(key, default) for key, default in RULE_DEFAULTS.items()}}
        if allocator_entry is not None:
            entry["allocator"] = allocator_entry
        if spec.get("initial_positions"):
            entry["initial_positions"] = spec["initial_positions"]
        if spec.get("information_end") is not None:
            entry["information_end"] = spec["information_end"]
        if spec.get("index"):
            entry["index"] = spec["index"]
        if spec.get("model_card"):
            from .ml.card import load_card

            if not isinstance(spec["model_card"], str):
                raise ValueError("model_card must be a models folder key")
            path = self.workspace.resolve(spec["model_card"])
            if not any(self.workspace.resolve(folder) in path.parents
                       for folder in self.workspace.folders["models"]):
                raise ValueError("model_card must be inside a configured models folder")
            try:
                card = load_card(path)
            except (OSError, KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Model card {spec['model_card']} is invalid: {exc}") from exc
            if spec.get("information_end") not in (None, card["information_end"]):
                raise ValueError("Cell information_end differs from its model card")
            entry["model_card"] = str(path)
            entry["information_end"] = card["information_end"]
        if spec.get("ask") is not None:
            from .ask import validate_settings

            grant = validate_settings(spec["ask"])
            if Path(grant["workspace"]).resolve() != self.workspace.root:
                raise ValueError("ask.workspace must be this cell library's workspace")
            # The approved script may be a sleeve's or the allocator's.
            allocator_script = (spec.get("allocator") or {}).get("script") \
                if isinstance(spec.get("allocator"), dict) else None
            approved = [sleeve for sleeve in [*sleeves, *([{"script": allocator_script}] if allocator_script else [])]
                        if hashlib.sha256(self.workspace.resolve(sleeve["script"]).read_bytes()).hexdigest()
                        == grant["approved_script_sha256"]]
            if not approved:
                raise ValueError("The script approved for context.ask changed")
            from .provenance import is_unreviewed

            for sleeve in approved:
                path = self.workspace.resolve(sleeve["script"])
                if is_unreviewed(self.workspace.root, path.read_bytes(), self.workspace.key(path)):
                    raise ValueError("A script an agent wrote must be reviewed before it may use context.ask")
            entry["ask"] = grant
        if entry["max_previous_volume_participation"] is None:
            del entry["max_previous_volume_participation"]
        _cells({"cells": [entry]})
        # A cell that replays decisions loads no script, so any folder will do.
        return entry, folders.pop() if folders else self.workspace.root


def _outside(sleeve: dict) -> bool:
    """Whether a sleeve's decisions come from outside Quantarium: a file, an inbox, or a decider."""
    return any(key in sleeve for key in ("decisions", "inbox", "decider"))


def _check_name(name: object) -> str:
    """A cell name: text of 1 to MAX_NAME characters, without control characters."""
    if not isinstance(name, str):
        raise ValueError("A cell name must be text")
    name = name.strip()
    if not name:
        raise ValueError("A cell needs a name")
    if len(name) > MAX_NAME or any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ValueError(f"A cell name has at most {MAX_NAME} characters and no control characters")
    return name


def _normalize(spec: dict) -> dict:
    """Keep only known fields, with symbols upper-cased and deduplicated in order."""
    if not isinstance(spec, dict):
        raise ValueError("spec must be an object")
    mode = spec.get("mode", "simple")
    if mode not in {"simple", "complex"}:
        raise ValueError("mode must be simple or complex")
    symbols: list[str] = []
    for symbol in spec.get("symbols", []):
        value = str(symbol).strip().upper()
        if value and value not in symbols:
            symbols.append(value)
    sleeves = []
    for sleeve in spec.get("sleeves", []):
        if not isinstance(sleeve, dict):
            raise ValueError("Each sleeve must be an object")
        if "decisions" in sleeve:
            # Decisions made outside Quantarium, replayed instead of a script.
            if not isinstance(sleeve["decisions"], str):
                raise ValueError("A decision sleeve names its file as a workspace key")
            sleeves.append({"decisions": sleeve["decisions"], "allocation": 1.0})
            continue
        if "decider" in sleeve:
            # An outside program the user registered, run session by session.
            if not isinstance(sleeve["decider"], str):
                raise ValueError("A decider sleeve names its decider")
            sleeves.append({"decider": sleeve["decider"], "allocation": 1.0})
            continue
        if "inbox" in sleeve:
            # Decisions submitted to a forward inbox before their sessions.
            if not isinstance(sleeve["inbox"], str):
                raise ValueError("An inbox sleeve names its inbox")
            sleeves.append({"inbox": sleeve["inbox"], "allocation": 1.0})
            continue
        parameters = sleeve.get("parameters", {})
        if not isinstance(parameters, dict):
            raise ValueError("Sleeve parameters must be an object")
        sleeves.append({"script": str(sleeve.get("script", "")), "class": str(sleeve.get("class", "")),
                        "allocation": float(sleeve.get("allocation", 1)), "parameters": parameters})
    result = {"mode": mode, "symbols": symbols,
              "initial_cash": float(spec.get("initial_cash", 10000)), "sleeves": sleeves,
              "initial_positions": copy.deepcopy(spec.get("initial_positions", {})),
              "information_end": spec.get("information_end"),
              "model_card": spec.get("model_card"), "ask": copy.deepcopy(spec.get("ask")),
              "allocator": copy.deepcopy(spec.get("allocator"))}
    for key, default in RULE_DEFAULTS.items():
        value = spec.get(key, default)
        result[key] = value if value is None else (int(value) if key == "rebalance_every_sessions"
                                                   else float(value))
    # Kept only when set, so specs saved before it compare unchanged.
    if spec.get("index"):
        result["index"] = str(spec["index"])
    return result
